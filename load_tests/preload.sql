\set ON_ERROR_STOP on

\if :{?row_count}
\else
\set row_count 1000000
\endif

\echo Preloading :row_count deterministic short URLs...

INSERT INTO urls (short_code, original_url)
SELECT
    'P' || lpad(to_hex(generated.row_number), 9, '0'),
    'https://example.com/preloaded/' || generated.row_number
FROM generate_series(1, :row_count::bigint) AS generated(row_number)
ON CONFLICT (short_code) DO NOTHING;

ANALYZE urls;
