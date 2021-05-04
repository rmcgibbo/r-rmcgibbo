CREATE TABLE IF NOT EXISTS nixpkgs_review_finished (
    id serial primary key,
    ctime timestamp with time zone not null,
    pull_request_number bigint not null,
    state text not null,
    system text not null,
    instance_type text,
    instance_id text,
    build_elapsed interval not null,
    report jsonb not null  /* number failed, succeeded, skipped, etc */
);
