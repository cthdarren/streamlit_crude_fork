# Copyright (c) Streamlit Inc. (2018-2022) Snowflake Inc. (2022-2025)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import math
import os
from typing import Final

import redis
from redis.exceptions import ConnectionError

from streamlit.logger import get_logger
from streamlit.runtime.caching.storage.cache_storage_protocol import (
    CacheStorage,
    CacheStorageContext,
    CacheStorageError,
    CacheStorageKeyNotFoundError,
    CacheStorageManager,
)

_LOGGER: Final = get_logger(__name__)


class RedisCacheStorageManager(CacheStorageManager):
    def create(self, context: CacheStorageContext) -> CacheStorage:
        """Skip the inmemorycache, directly hit redis server on every call"""
        return RedisCacheStorage(context=context)

    def clear_all(self) -> None:
        pass

    def check_context(self, context: CacheStorageContext) -> None:
        if (
            context.persist == "disk"
            and context.ttl_seconds is not None
            and not math.isinf(context.ttl_seconds)
        ):
            _LOGGER.warning(
                f"The cached function '{context.function_display_name}' has a TTL "
                "that will be ignored. Persistent cached functions currently don't "
                "support TTL."
            )


class RedisCacheStorage(CacheStorage):
    """Cache storage that persists data to redis"""

    def __init__(self, context: CacheStorageContext):
        self.function_key = context.function_key
        self.persist = context.persist
        self._ttl_seconds = context.ttl_seconds
        # TODO: Possibly unneeded
        # self._max_entries = context.max_entries

        try:
            redis_host: str = os.getenv("REDIS_HOST", "localhost")
            redis_port: int = int(os.getenv("REDIS_PORT", 6379))
            redis_db: int = int(os.getenv("REDIS_DB", 0))
        except ValueError:
            _LOGGER.error(
                "Type mismatch for redis env variables, redis_port and redis_db should be parseable as int"
            )
            raise ValueError(
                "Type mismatch for redis env variables, redis_port and redis_db should be parseable as int"
            )

        self.conn = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
        )
        _LOGGER.info(self.conn)

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds if self._ttl_seconds is not None else math.inf

    # TODO: Possibly unneeded
    # @property
    # def max_entries(self) -> float:
    #     return float(self._max_entries) if self._max_entries is not None else math.inf

    def get(self, key: str) -> bytes:
        """
        Returns the stored value for the key if persisted,
        raise CacheStorageKeyNotFoundError if not found, or not configured
        with persist="disk"
        """
        if self.persist == "redis":
            try:
                value = self.conn.get(key)
                _LOGGER.debug(f"REDIS CACHE HIT: {key}")
                if value is None:
                    raise CacheStorageKeyNotFoundError("Key not found in redis cache")
                return bytes(value)
            except ConnectionError as ex:
                _LOGGER.exception(
                    "Error connecting to Redis server. Is it currently running?"
                )

                # Don't raise because we don't want the server to crash, but
                # to just run the function on each call regardless if the server is unreachable
                raise CacheStorageKeyNotFoundError(
                    "Error connecting to Redis server. Is it currently running?"
                ) from ex
        else:
            raise CacheStorageKeyNotFoundError(
                f"Redis cache storage is disabled (persist={self.persist})"
            )

    def set(self, key: str, value: bytes) -> None:
        """Sets the value for a given key"""
        if self.persist == "redis":
            try:
                self.conn.set(key, value)
                if self.ttl_seconds != math.inf:
                    self.conn.expire(key, self.ttl_seconds)
                _LOGGER.debug(f"REDIS CACHE WRITTEN: {key}")
            except ConnectionError:
                _LOGGER.exception(
                    "Error connecting to Redis server. Is it currently running?"
                )
                # Don't raise because we don't want the server to crash, but
                # to just run the function on each call regardless if the server is unreachable

                # raise CacheStorageError(
                #     "Error connecting to Redis server. Is it currently running?"
                # ) from ex
            except Exception as ex:
                _LOGGER.exception("Unable to write to cache", exc_info=ex)
                raise CacheStorageError("Unable to write to cache") from ex

    def delete(self, key: str) -> None:
        """Delete a cache file from redis. If the key does not exist on redis,
        return silently. If another exception occurs, log it. Does not throw.
        """
        if self.persist == "redis":
            try:
                self.conn.delete(key)
            except ConnectionError as ex:
                _LOGGER.exception(
                    "Error connecting to Redis server. Is it currently running?"
                )
                raise CacheStorageError(
                    "Error connecting to Redis server. Is it currently running?"
                ) from ex
            except Exception as ex:
                _LOGGER.exception(
                    "Unable to remove a file from the redis cache", exc_info=ex
                )

    def clear(self) -> None:
        """Delete all keys from redis by running the flushdb command"""
        if self.persist == "redis":
            try:
                self.conn.execute_command("flushdb")
            except ConnectionError as ex:
                _LOGGER.exception(
                    "Error connecting to Redis server. Is it currently running?"
                )
                raise CacheStorageError(
                    "Error connecting to Redis server. Is it currently running?"
                ) from ex
            except Exception as ex:
                _LOGGER.exception("Unable to clear redis cache", exc_info=ex)

    def close(self) -> None:
        """Dummy implementation of close, we don't need to actually "close" anything"""
