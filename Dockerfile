FROM ubuntu:latest

RUN apt-get update && \
    apt-get install -y python3-pip inotify-tools && \
    pip3 install azure-storage-blob==12.8.1 inotify==0.2.10
