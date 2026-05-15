"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import EmptyState from "@/components/EmptyState";
import InlineError from "@/components/InlineError";
import { themesAPI, ReadingCluster } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { useRouter } from "next/navigation";

export default function ThemesPage() {
  const { user, isLoading: authLoading } = useAuth();
  const router = useRouter();
  const [clusters, setClusters] = useState<ReadingCluster[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/login");
    }
  }, [user, authLoading, router]);

  useEffect(() => {
    if (!user) return;
    themesAPI
      .getClusters()
      .then((data) => setClusters(data.clusters))
      .catch(() => setError("Couldn't load reading themes. Try again."))
      .finally(() => setLoading(false));
  }, [user]);

  return (
    <div className="min-h-screen bg-[var(--color-bg-primary)]">
      <Navbar />
      <main className="max-w-3xl mx-auto px-5 sm:px-6 lg:px-8 py-8">
        <h1 className="font-serif text-3xl font-normal text-[var(--color-text-primary)] mt-6 mb-6">
          Reading themes
        </h1>

        {loading && (
          <div className="text-[var(--color-text-muted)] text-sm py-8 text-center">
            Loading themes...
          </div>
        )}

        {error && !loading && (
          <InlineError
            message={error}
            onDismiss={() => setError(null)}
            onRetry={() => {
              setError(null);
              setLoading(true);
              themesAPI
                .getClusters()
                .then((data) => setClusters(data.clusters))
                .catch(() =>
                  setError("Couldn't load reading themes. Try again."),
                )
                .finally(() => setLoading(false));
            }}
          />
        )}

        {!loading && !error && clusters?.length === 0 && (
          <EmptyState
            message="No reading themes yet"
            description="Themes emerge after you've saved at least 10 articles with semantic tags. Keep reading."
            variant="bordered"
          />
        )}

        {!loading && !error && clusters && clusters.length > 0 && (
          <div className="grid gap-4 sm:grid-cols-2">
            {clusters.map((cluster) => (
              <ClusterCard key={cluster.id} cluster={cluster} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

function ClusterCard({ cluster }: { cluster: ReadingCluster }) {
  return (
    <div className="border border-[var(--color-border)] p-4 hover:bg-[var(--color-bg-secondary)] transition-colors">
      <h2 className="font-serif text-lg font-normal text-[var(--color-text-primary)] capitalize mb-1">
        {cluster.label}
      </h2>
      <p className="text-xs text-[var(--color-text-muted)] mb-3">
        {cluster.article_count}{" "}
        {cluster.article_count === 1 ? "article" : "articles"}
      </p>

      <div className="flex flex-wrap gap-1 mb-3">
        {cluster.tag_labels.slice(0, 5).map((tag) => (
          <span
            key={tag}
            className="text-xs px-1.5 py-0.5 border border-[var(--color-border)] text-[var(--color-text-muted)]"
          >
            {tag}
          </span>
        ))}
      </div>

      <div className="space-y-1">
        {cluster.top_articles.map((article) => (
          <Link
            key={article.id}
            href={`/content/${article.id}`}
            className="block text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] truncate transition-colors"
          >
            {article.title ?? "Untitled"}
          </Link>
        ))}
      </div>
    </div>
  );
}
