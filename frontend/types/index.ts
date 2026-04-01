export interface User {
  id: string;
  email: string;
  username: string | null;
  full_name: string | null;
  is_active: boolean;
  is_public: boolean;
  is_queue_public: boolean;
  is_crates_public: boolean;
  is_verified: boolean;
  created_at: string;
}

export interface ContentItem {
  id: string;
  user_id: string;
  original_url: string;
  title: string | null;
  description: string | null;
  summary?: string | null;
  thumbnail_url: string | null;
  tags: string[] | null;
  auto_tags?: string[] | null;
  full_text: string | null;
  word_count: number | null;
  reading_time_minutes: number | null;
  is_academic?: boolean;
  content_type: "article" | "video" | "pdf" | "tweet" | "unknown";
  content_vertical: "general" | "academic" | "recipe" | "repository" | string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  vertical_metadata?: Record<string, any>;
  author: string | null;
  published_date: string | null;
  is_read: boolean;
  is_archived: boolean;
  is_public: boolean;
  read_position?: number;
  reading_status: "unread" | "in_progress" | "read" | "archived";
  processing_status: string;
  processing_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface VinylTrack {
  position: string;
  title: string;
  duration: string | null;
}

export interface VinylVideo {
  title: string | null;
  uri: string;
  duration: number | null;
}

export interface VinylRecord {
  id: string;
  user_id: string;
  discogs_url: string;
  discogs_release_id: number | null;
  title: string | null;
  artist: string | null;
  label: string | null;
  catalog_number: string | null;
  year: number | null;
  cover_url: string | null;
  genres: string[];
  styles: string[];
  tracklist: VinylTrack[];
  videos: VinylVideo[];
  notes: string | null;
  rating: number | null;
  tags: string[];
  status: "collection" | "wantlist" | "library";
  is_public: boolean;
  processing_status: string;
  processing_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface List {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  is_shared: boolean;
  is_public: boolean;
  created_at: string;
  updated_at: string;
  content_count?: number;
}

export interface Draft {
  id: string;
  list_id: string;
  user_id: string;
  title: string | null;
  content: string;
  word_count: number;
  created_at: string;
  updated_at: string;
}

export interface Highlight {
  id: string;
  content_item_id: string;
  user_id: string;
  text: string;
  note: string | null;
  start_offset: number;
  end_offset: number;
  color: string;
  created_at: string;
}
