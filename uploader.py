#!/usr/bin/env python3

import json
import logging
import os
import os.path
import pwd
import re

from pathlib import Path
from time import sleep
from typing import Dict, List, Optional, Tuple, TypedDict, Union, cast

import inotify.adapters

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobClient

logging.basicConfig(level=logging.WARNING)

UPLOAD_ATTEMPTS = 3
USERS = {f"{usr.pw_dir}/": usr for usr in pwd.getpwall() if usr.pw_uid >= 4000}  # type: Dict[str, pwd.struct_passwd]


# Typehints for old v1 config and v2 config
class ConfigV1Value(TypedDict):
    path: str
    dsn: str
    container: str
    slug: str


class ConfigV2SimpleValue(ConfigV1Value):
    type: str


class ConfigV2RegexValue(TypedDict):
    type: str
    base_path: str
    regex: str
    dsn: str
    container: str
    dest_path: str


ConfigV1 = Dict[str, ConfigV1Value]


class ConfigV2(TypedDict):
    version: int
    watches: List[Union[ConfigV2SimpleValue, ConfigV2RegexValue]]
    directories: List[str]


def get_config(filepath: str = "config.json") -> Tuple[int, dict]:
    """
    Reads config from file and gets config version
    """
    with open(filepath, "r") as fp:
        config = json.load(fp)

    assert isinstance(config, dict)
    version = config.get("version", 1)

    return version, config


def make_dir(directory: str, user: Optional[pwd.struct_passwd] = None) -> bool:
    """
    Makes a directory

    Didnt use ok.makedirs as it doesnt chmod & chown. Setting umask doesnt
    help this either sadly.

    Basically goes through users in /etc/passwd, checks if their home and the
    directory shares a common prefix and that prefix is the home directory. Uses
    that user as the uid/gid.
    """
    if not user:
        for user_home, user_struct in USERS.items():
            if not os.path.commonprefix([directory, user_home]) == user_home:
                continue

            user = user_struct
            break
        else:
            logging.warning(f"Failed to make directory {directory} as could not find the owning user")
            return False

    # User found by now.
    parent_dir = os.path.dirname(directory)
    if not os.path.exists(parent_dir) and not make_dir(parent_dir, user):
        return False

    # Parent directory exists by now
    if not os.path.exists(directory):
        os.mkdir(directory, 0o700)
        logging.warning(f"Created directory {directory} for {user.pw_name}")
    os.chown(directory, user.pw_uid, user.pw_gid)
    return True


def upload_file(src_path: str, dsn: str, container: str, dest_path: str) -> None:
    """
    Uploads a file to blob storage, retries a few times if needed.
    """
    for attempt in range(UPLOAD_ATTEMPTS):
        try:
            blob = BlobClient.from_connection_string(conn_str=dsn, container_name=container, blob_name=dest_path)
            with open(src_path, "rb") as fp:
                logging.warning(f"Uploading: [{src_path}] to [{container}] as [{dest_path}]")
                blob.upload_blob(fp)
                logging.warning(f"Uploaded: [{src_path}] to [{container}] as [{dest_path}]")
            os.remove(src_path)
            logging.warning(f"Removed: [{src_path}]")
            break
        except ResourceExistsError:
            logging.error(f"File with name [{dest_path}] already exists")
            break
        except ResourceNotFoundError:
            logging.error(f"Container: [{container}] does not exist")
            break
        except Exception as e:
            logging.error(e)
            pass
        sleep(10)

    return None


def get_watch_job(path: str, config: ConfigV2) -> Tuple[Optional[ConfigV2SimpleValue], Optional[ConfigV2RegexValue]]:
    """
    Finds a watch job from a list based on a filepath given
    """
    for watch_job in config["watches"]:
        if watch_job["type"] == "simple":
            watch_job_simple = cast(ConfigV2SimpleValue, watch_job)
            watch_job_simple_path = watch_job_simple["path"].rstrip("/") + "/"

            prefix = os.path.commonprefix([watch_job_simple["path"], path])
            print(f"prefix: {prefix}, base: {watch_job_simple_path}, path: {path}")
            if os.path.commonprefix([watch_job_simple_path, path]) == watch_job_simple_path:
                return watch_job_simple, None

        elif watch_job["type"] == "regex":
            watch_job_regex = cast(ConfigV2RegexValue, watch_job)
            watch_job_regex_path = watch_job_regex["base_path"].rstrip("/") + "/"

            prefix = os.path.commonprefix([watch_job_regex_path, path])
            print(f"prefix: {prefix}, base: {watch_job_regex_path}, path: {path}")
            if os.path.commonprefix([watch_job_regex_path, path]) == watch_job_regex_path:
                return None, watch_job_regex

    return None, None


def watch_directory_recursively(watcher: inotify.adapters.Inotify, base: str) -> None:
    for root, _, _ in os.walk(base):
        watcher.add_watch(root)
    return None


def run_version2(watcher: inotify.adapters.Inotify, config: ConfigV2) -> None:
    """
    Watch files for version 2
    """
    for directory in config["directories"]:
        make_dir(directory)

    for watch_job in config["watches"]:
        if watch_job["type"] == "simple":
            watch_job_simple = cast(ConfigV2SimpleValue, watch_job)
            watch_directory_recursively(watcher, watch_job_simple["path"])
        elif watch_job["type"] == "regex":
            watch_job_regex = cast(ConfigV2RegexValue, watch_job)
            watch_directory_recursively(watcher, watch_job_regex["base_path"])
        else:
            logging.warning(f"Unknown watch job type: {watch_job['type']}")

    for _, type_names, path, filename in watcher.event_gen(yield_nones=False):
        filepath = os.path.join(path, filename)

        # print(f"event: {type_names}, {path}, {filename}")
        if "IN_CREATE" in type_names and "IN_ISDIR" in type_names:  # Directory was created
            watcher.add_watch(filepath)
            logging.warning(f"Watching new directory {filepath}")
            continue

        if "IN_CLOSE_WRITE" not in type_names:  # Skip anything else as we're after events after a file has been written
            continue

        simple_conf, regex_conf = get_watch_job(filepath, config)

        if simple_conf:  # Process simple files put in directory
            slug = simple_conf["slug"]
            blob_name = f"{slug}/{filename}"

            upload_file(filepath, simple_conf["dsn"], simple_conf["container"], blob_name)

        elif regex_conf:  # Check if filepath matches regex
            local_path = filepath.replace(regex_conf["base_path"], "").lstrip("/")
            match = re.match(regex_conf["regex"], local_path)
            if not match:
                logging.warning(f"No watches to cover file: {filename}")
                continue

            match_data = match.groupdict()
            match_data["filename"] = filename
            blob_name = regex_conf["dest_path"].format(**match_data)
            upload_file(filepath, regex_conf["dsn"], regex_conf["container"], blob_name)

        else:
            logging.warning(f"No watches to cover file: {filename}")

    return None


def run_version1(watcher: inotify.adapters.Inotify, config: ConfigV1) -> None:
    """
    Watch files for version 1
    """
    for data in config.values():
        watcher.add_watch(data["path"])

    for _, type_names, path, filename in watcher.event_gen(yield_nones=False):
        if "IN_CLOSE_WRITE" not in type_names:
            continue

        user = Path(path).parts[2]
        dsn = config[user]["dsn"]
        container = config[user]["container"]
        slug = config["user"]["slug"]
        blob_name = f"{slug}/{filename}"
        filepath = os.path.join(path, filename)

        upload_file(filepath, dsn, container, blob_name)

    return None


def main() -> None:
    version, config_dict = get_config()

    watcher = inotify.adapters.Inotify()

    if version == 1:
        run_version1(watcher, cast(ConfigV1, config_dict))
    elif version == 2:
        run_version2(watcher, cast(ConfigV2, config_dict))
    else:
        logging.error(f"Unhandled config version {version}")

    return None


if __name__ == "__main__":
    main()
