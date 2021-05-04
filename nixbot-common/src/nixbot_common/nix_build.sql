/* Populated by https://github.com/rmcgibbo/post-build-postgres
   which is a service automatically running on our build machines,
   but I've just copied it here for completeness.
*/
CREATE TABLE IF NOT EXISTS nix_build (
  id serial primary key,
  name text not null,
  drv_path text not null,
  out_paths text[] not null,
  ctime timestamp with time zone not null,
  build_elapsed interval not null,
  instance_type text,
  instance_id text,
  pull_request_number bigint
);