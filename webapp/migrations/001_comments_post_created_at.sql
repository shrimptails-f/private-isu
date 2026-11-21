ALTER TABLE comments ADD INDEX idx_comments_post_created_at (post_id, created_at);
