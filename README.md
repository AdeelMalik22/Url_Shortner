# URL Shortener

A URL Shortener is a web service that converts long URLs into short, easy-to-share links. It creates a unique short code for every URL and stores the mapping between the short code and the original URL.

When a user opens the generated short URL, the service looks up the original URL and redirects the user to the destination.

## Features

* Create short URLs from long URLs
* Generate unique short codes
* Redirect users from short links to original URLs
* Store URL mappings in a database
* Validate URLs before creating short links
* Prevent duplicate short codes

## How it works

1. User sends a long URL to the service.
2. The system generates a unique short identifier.
3. The identifier and original URL are stored in the database.
4. The service returns a short URL.
5. When the short URL is visited, the system retrieves the original URL and redirects the user.

## Example

Long URL:

`https://example.com/products/category/electronics/mobile-phone`

Generated short URL:

`https://short.com/a8Df91`

Opening the short URL redirects the user to the original URL.

## Technology Stack

* FastAPI
* PostgreSQL
* SQLAlchemy
* Alembic
* Python

## Future Improvements

* Redis caching for faster redirects
* Click analytics
* URL expiration
* User authentication
* Custom short URLs
* Rate limiting
