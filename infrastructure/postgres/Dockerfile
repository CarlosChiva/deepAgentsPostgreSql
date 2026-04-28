FROM postgres:16-alpine

# Install pgvector extension for PostgreSQL 16
# Alpine package naming: postgresql16-pgvector (not postgresql-16-vector)
RUN apk add --no-cache postgresql16-pgvector

# Set locale and collation for proper text handling
ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8
ENV LANGUAGES=en_US.UTF-8

# Set default text search configuration to English
ENV TEXTCONFIG=english

EXPOSE 5432
