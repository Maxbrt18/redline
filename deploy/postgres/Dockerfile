FROM postgres:17-bookworm
COPY --chmod=0644 pg_hba.conf /etc/postgresql/pg_hba.conf
CMD ["postgres", "-c", "hba_file=/etc/postgresql/pg_hba.conf"]
