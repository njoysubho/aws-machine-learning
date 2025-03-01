FROM ghcr.io/berriai/litellm:main-latest

WORKDIR /app
COPY litellm_config.yml .

#RUN chmod +x entrypoint.sh
# pass aws secrets via env
ENV AWS_ACCESS_KEY_ID=""
ENV AWS_SECRET_ACCESS_KEY=""
ENV AWS_REGION="eu-west-1"
ENV LITELLM_MASTER_KEY="sk-sab2301"
ENV UI_USERNAME="admin"
ENV UI_PASSWORD="admin"
ENV DATABASE_URL=""
CMD [ "--port","4000","--config","litellm_config.yml","--detailed_debug","--run_gunicorn" ]

