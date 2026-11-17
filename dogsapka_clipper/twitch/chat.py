"""Read-only Twitch chat client using Twitch's official IRC interface.

Only aggregate statistics are needed for highlight detection, so usernames are reduced
to a salted, per-process hash and message text is never persisted.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import ssl
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
_SALT = os.urandom(16)


@dataclass
class ChatMessage:
    ts: float
    user_hash: str
    text: str
    emotes: list[str] = field(default_factory=list)


def anonymize(user: str) -> str:
    return hashlib.blake2b(user.lower().encode(), key=_SALT, digest_size=8).hexdigest()


def parse_privmsg(line: str, now: float | None = None) -> ChatMessage | None:
    """Parse a raw IRC line (with optional IRCv3 tags) into a ChatMessage."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for kv in raw_tags.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
    if " PRIVMSG " not in line or not line.startswith(":"):
        return None
    prefix, _, rest = line[1:].partition(" ")
    user = prefix.split("!", 1)[0]
    _, _, trailing = rest.partition(" :")
    text = trailing.rstrip("\r\n")
    emotes: list[str] = []
    if tags.get("emotes"):
        # format: id:0-4,6-10/id2:12-15
        for part in tags["emotes"].split("/"):
            _id, _, ranges = part.partition(":")
            for rng in ranges.split(","):
                try:
                    a, b = (int(x) for x in rng.split("-"))
                    emotes.append(text[a:b + 1])
                except ValueError:
                    continue
    return ChatMessage(ts=now or time.time(), user_hash=anonymize(tags.get("user-id") or user), text=text, emotes=emotes)


class TwitchChatReader:
    """Connects to a channel's chat and calls ``on_message`` for every message.

    If ``oauth_token`` is None, Twitch's documented anonymous read-only login is used.
    """

    def __init__(self, channel: str, on_message: Callable[[ChatMessage], Awaitable[None] | None],
                 oauth_token: str | None = None, nick: str | None = None,
                 on_status: Callable[[str, str], None] | None = None) -> None:
        self.channel = channel.lower().lstrip("#")
        self.on_message = on_message
        self.oauth_token = oauth_token
        self.nick = nick
        self.on_status = on_status or (lambda lvl, msg: None)
        self._stop = asyncio.Event()
        self.connected = False

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # network errors -> reconnect
                self.connected = False
                self.on_status("warning", f"Chat connection to #{self.channel} lost ({e.__class__.__name__}); "
                                          f"reconnecting in {backoff:.0f}s")
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff + random.random())
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 60.0)

    async def _session(self) -> None:
        ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(asyncio.open_connection(IRC_HOST, IRC_PORT, ssl=ctx), 15)
        try:
            if self.oauth_token and self.nick:
                writer.write(f"PASS oauth:{self.oauth_token}\r\nNICK {self.nick.lower()}\r\n".encode())
            else:
                writer.write(f"NICK justinfan{random.randint(10000, 99999)}\r\n".encode())
            writer.write(b"CAP REQ :twitch.tv/tags\r\n")
            writer.write(f"JOIN #{self.channel}\r\n".encode())
            await writer.drain()
            self.connected = True
            self.on_status("info", f"Connected to #{self.channel} chat")
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=360)
                except asyncio.TimeoutError:
                    raise ConnectionError("chat timeout")
                if not raw:
                    raise ConnectionError("closed by server")
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("PING"):
                    writer.write(line.replace("PING", "PONG", 1).encode() + b"\r\n")
                    await writer.drain()
                    continue
                if " RECONNECT" in line:
                    raise ConnectionError("server requested reconnect")
                if "Login authentication failed" in line:
                    self._stop.set()
                    self.on_status("error", "Twitch chat login failed - reconnect your Twitch account.")
                    return
                msg = parse_privmsg(line)
                if msg:
                    res = self.on_message(msg)
                    if asyncio.iscoroutine(res):
                        await res
        finally:
            self.connected = False
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
