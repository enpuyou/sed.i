import logging
import re
import requests
from uuid import UUID
from app.core.celery_app import celery_app
from app.core.config import settings
from app.models.vinyl import VinylRecord
from app.tasks.base import DatabaseTask


logger = logging.getLogger(__name__)


@celery_app.task(base=DatabaseTask, bind=True, max_retries=3)
def fetch_discogs_metadata(self, vinyl_record_id: str):
    """
    Fetch metadata from Discogs for a vinyl record.
    """
    try:
        # Get record from database
        record = (
            self.db.query(VinylRecord)
            .filter(VinylRecord.id == UUID(vinyl_record_id))
            .first()
        )
        if not record:
            logger.error(f"Vinyl record {vinyl_record_id} not found")
            return

        # Update status to processing
        record.processing_status = "processing"
        self.db.commit()

        # Parse release ID from Discogs URL
        # Matches: https://www.discogs.com/release/123456-Artist-Title
        # or: https://www.discogs.com/master/123456...
        release_id_match = re.search(r"/release/(\d+)", record.discogs_url)
        if not release_id_match:
            record.processing_status = "failed"
            record.processing_error = "Could not parse Discogs release ID from URL"
            self.db.commit()
            return

        release_id = release_id_match.group(1)
        record.discogs_release_id = int(release_id)

        # Call Discogs API
        headers = {
            "User-Agent": "sed.i/1.0",
        }
        params = {}
        if settings.DISCOGS_TOKEN:
            params["token"] = settings.DISCOGS_TOKEN

        url = f"https://api.discogs.com/releases/{release_id}"
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Map Discogs data to model
        record.title = data.get("title")
        record.artist = data.get("artists_sort") or (
            data.get("artists")[0].get("name") if data.get("artists") else None
        )
        labels = data.get("labels", [])
        record.label = labels[0].get("name") if labels else None
        record.catalog_number = labels[0].get("catno") if labels else None
        record.year = data.get("year")

        # Images
        # Use uri150 (thumbnail) — sized for display and lighter than full-res.
        # Append the token so the URL is directly browser-loadable; without it
        # Discogs returns 401 when the browser requests the image.
        if data.get("images"):
            primary_image = next(
                (img for img in data["images"] if img.get("type") == "primary"),
                data["images"][0],
            )
            img_url = primary_image.get("uri150") or primary_image.get("uri")
            if img_url and settings.DISCOGS_TOKEN:
                img_url = f"{img_url}?token={settings.DISCOGS_TOKEN}"
            record.cover_url = img_url

        # Genres and Styles
        record.genres = data.get("genres", [])
        record.styles = data.get("styles", [])

        # Tracklist
        tracklist = []
        for track in data.get("tracklist", []):
            tracklist.append(
                {
                    "position": track.get("position", ""),
                    "title": track.get("title", ""),
                    "duration": track.get("duration", ""),
                }
            )
        record.tracklist = tracklist

        # Videos (YouTube links from Discogs, deduplicated by URI)
        videos = []
        seen_uris = set()
        for video in data.get("videos", []):
            uri = video.get("uri", "")
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                videos.append(
                    {
                        "title": video.get("title", ""),
                        "uri": uri,
                        "duration": video.get("duration"),
                    }
                )
        record.videos = videos

        # Master ID
        record.discogs_master_id = data.get("master_id")

        record.processing_status = "completed"
        record.processing_error = None
        self.db.commit()

        logger.info(
            f"Successfully fetched Discogs metadata for {record.title} by {record.artist}"
        )
        return {"id": vinyl_record_id, "status": "completed"}

    except requests.RequestException as e:
        logger.warning(f"Discogs API request failed for {vinyl_record_id}: {str(e)}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))

    except Exception as e:
        logger.error(
            f"Failed to fetch Discogs metadata for {vinyl_record_id}: {str(e)}"
        )
        if record:
            record.processing_status = "failed"
            record.processing_error = str(e)
            self.db.commit()
        return {"id": vinyl_record_id, "status": "failed", "error": str(e)}
