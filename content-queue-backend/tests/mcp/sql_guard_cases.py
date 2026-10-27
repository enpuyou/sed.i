"""SQL shapes for the query_library isolation gate (shared by tests and audits)."""

D = "deleted_at IS NULL"

BYPASSES = {
    "or_true": f"SELECT title FROM content_items WHERE user_id = :user_id OR true AND {D}",
    "or_1_eq_1": f"SELECT title FROM content_items WHERE (user_id = :user_id OR 1=1) AND {D}",
    "or_not_null": f"SELECT title FROM content_items WHERE (user_id = :user_id OR user_id IS NOT NULL) AND {D}",
    "select_list_only": f"SELECT (user_id = :user_id) AS mine, title FROM content_items WHERE {D}",
    "case_expr": f"SELECT title FROM content_items WHERE CASE WHEN user_id = :user_id THEN true ELSE true END AND {D}",
    "function_arg": f"SELECT title FROM content_items WHERE coalesce(user_id = :user_id, true) AND {D}",
    "having_only": f"SELECT user_id, count(*) FROM content_items WHERE {D} GROUP BY user_id HAVING user_id = :user_id",
    "order_by_only": f"SELECT title FROM content_items WHERE {D} ORDER BY user_id = :user_id",
    "under_not": f"SELECT title FROM content_items WHERE NOT (user_id = :user_id) AND {D}",
    "or_branch_parens": f"SELECT title FROM content_items WHERE ((user_id = :user_id) OR (title = 'x')) AND {D}",
    "union_side": f"SELECT title FROM content_items WHERE user_id = :user_id AND {D} UNION SELECT title FROM content_items",
    "subquery_not_own_block": (
        f"SELECT title FROM content_items WHERE {D} AND id IN "
        "(SELECT content_item_id FROM highlights WHERE user_id = :user_id)"
    ),
    "left_join_on_only": (
        "SELECT ci.title FROM highlights h LEFT JOIN content_items ci "
        f"ON ci.user_id = :user_id AND ci.id = h.content_item_id WHERE h.user_id = :user_id"
    ),
    "trashed_not_excluded": "SELECT title FROM content_items WHERE user_id = :user_id",
}

LEGIT = {
    "single": f"SELECT title FROM content_items WHERE user_id = :user_id AND {D}",
    "unqualified_single": f"SELECT title FROM content_items WHERE {D} AND user_id = :user_id LIMIT 5",
    "join_aliases": (
        "SELECT ci.title, h.text FROM content_items ci "
        "JOIN highlights h ON h.content_item_id = ci.id "
        f"WHERE ci.user_id = :user_id AND ci.deleted_at IS NULL AND h.user_id = :user_id"
    ),
    "bridge": (
        "SELECT ci.title FROM lists l "
        "JOIN content_list_membership m ON m.list_id = l.id "
        "JOIN content_items ci ON ci.id = m.content_item_id "
        "WHERE l.owner_id = :user_id AND ci.user_id = :user_id AND ci.deleted_at IS NULL"
    ),
    "and_chain_parens": (
        f"SELECT title FROM content_items WHERE (user_id = :user_id AND (is_read = false AND {D}))"
    ),
    "reversed_eq": f"SELECT title FROM content_items WHERE :user_id = user_id AND {D}",
    "inner_join_on": (
        "SELECT ci.title FROM highlights h "
        "INNER JOIN content_items ci ON ci.id = h.content_item_id AND ci.user_id = :user_id AND ci.deleted_at IS NULL "
        "WHERE h.user_id = :user_id"
    ),
    "cte_each_block": (
        f"WITH recent AS (SELECT id, title FROM content_items WHERE user_id = :user_id AND {D}) "
        "SELECT r.title, h.text FROM recent r JOIN highlights h ON h.content_item_id = r.id "
        "WHERE h.user_id = :user_id"
    ),
    "subquery_each_block": (
        f"SELECT title FROM content_items WHERE user_id = :user_id AND {D} AND id IN "
        "(SELECT content_item_id FROM highlights WHERE user_id = :user_id)"
    ),
    "lists_no_soft_delete": "SELECT name FROM lists WHERE owner_id = :user_id",
}
