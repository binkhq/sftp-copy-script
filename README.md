# Watches directories and copies files to blob storage

^^ as the title said.

Needs `inotify` and `azure-storage-blob`. Expects a file `config.json` to be in the current working directory when the script is ran.

All keys are required. `slug` is used to determine the blob storage upload prefix.

Config:
```json
{
  "watches": [
    {
      "type": "simple",
      "path": "/home/some_username/uploads",
      "dsn": "blobstoragedsn",
      "container": "blob storage container",
      "slug": "upload/slug"
    },
    {
      "type": "regex",
      "base_path": "/home/some_username/uploads2",
      "regex": "(?P<folder>.+?)/(?P<something>.+).txt",
      "dsn": "blobstoragedsn",
      "container": "blob storage container",
      "dest_path": "upload/{folder}/{filename}"
    }
  ],
  "directories": [
    "/home/some_username/uploads/test1"
  ]
}
```

So the simple watch type just monitors a directory and uploads the filename prefixed by the slug. 

The regex one will watch a base path, and match regex's against the filepath after the base path. I.e if you are watching /home/test and a file 
gets uploaded to `/home/test/lol/n00b.txt` then the regex will be provided `lol/n00b.txt`. The destination path can be templated and will be provided
the filename (not including any path) and any capturing groups from the matched regex. This also assumes that none of the paths nest within one another, 
that they are all distinct paths. 

The directories list will make directories if they don't exist, and `chown` them to the encompasing user and `chmod` them to `0700`. The 
assumption has been made that the directories will reside in `/home/user/*` and that user IDs are >= 4000.

# Testing

Run `make test` to launch a container. Make a config.json in the current directory (which would be this git repo).
Make any users home dirs specified in the base paths, i.e. `useradd -u 4000 -m user1`.
Run `python3 uploader.py`

Then exec into the container with `docker exec -it sftp bash`