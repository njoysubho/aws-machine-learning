#!/bin/bash
docker run \
    -e AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID} \
    -e AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}\
    -p 4000:4000 \
    local-llm:latest
