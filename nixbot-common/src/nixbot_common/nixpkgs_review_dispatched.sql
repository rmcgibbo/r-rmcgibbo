CREATE TABLE IF NOT EXISTS nixpkgs_review_dispatched (
    id serial primary key,
    ctime timestamp with time zone not null,
    pull_request_number bigint not null,
    state text not null,  /* == "dispatched" */
    ofborg_eval_url text not null,
    num_packages jsonb not null
);
