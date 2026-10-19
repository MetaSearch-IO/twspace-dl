"""Stream lookup with a guest token, and audio-only download of video Spaces."""

from unittest.mock import MagicMock

import pytest
import requests

from twspace_dl import twspace_dl as twspace_dl_module
from twspace_dl.api import API, HTTPClient, LiveVideoStreamAPI, http_status
from twspace_dl.twspace_dl import TwspaceDL

COOKIES = {"auth_token": "a" * 40, "ct0": "b" * 160}
MEDIA_KEY = "28_2110791340913238016"
STATUS = {"source": {"location": "https://cdn/x/audio-space/playlist_1.m3u8"}}


def _json_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def _http_failure(status_code):
    """What `HTTPClient.get()` raises for a 4xx: RuntimeError from HTTPError."""
    response = requests.Response()
    response.status_code = status_code
    try:
        raise requests.exceptions.HTTPError(response=response)
    except requests.exceptions.HTTPError as http_error:
        try:
            raise RuntimeError("API request failed with HTTP error") from http_error
        except RuntimeError as err:
            return err


def _stream_api(*get_results):
    client = MagicMock(spec=HTTPClient)
    client.session = MagicMock()
    client.session.post.side_effect = [
        _json_response({"guest_token": f"gt{i}"}) for i in range(5)
    ]
    client.get.side_effect = [
        r if isinstance(r, Exception) else _json_response(r) for r in get_results
    ]
    return client, LiveVideoStreamAPI(client, "1.1/live_video_stream", COOKIES)


def test_status_uses_guest_token_and_no_cookies():
    client, api = _stream_api(STATUS, STATUS)

    assert api.status(MEDIA_KEY) == STATUS
    assert api.status(MEDIA_KEY) == STATUS

    url = client.get.call_args.args[0]
    kwargs = client.get.call_args.kwargs
    assert url == f"https://x.com/i/api/1.1/live_video_stream/status/{MEDIA_KEY}"
    assert kwargs["headers"]["x-guest-token"] == "gt0"
    assert "x-csrf-token" not in kwargs["headers"]
    assert "cookies" not in kwargs
    assert client.session.post.call_count == 1


@pytest.mark.parametrize("status_code", [401, 403])
def test_rejected_guest_token_is_renewed_once(status_code):
    client, api = _stream_api(_http_failure(status_code), STATUS)

    assert api.status(MEDIA_KEY) == STATUS
    assert client.session.post.call_count == 2
    assert client.get.call_args.kwargs["headers"]["x-guest-token"] == "gt1"


def test_other_errors_are_not_retried():
    client, api = _stream_api(_http_failure(404))

    with pytest.raises(RuntimeError):
        api.status(MEDIA_KEY)
    assert client.get.call_count == 1


def test_second_rejection_is_raised():
    client, api = _stream_api(_http_failure(403), _http_failure(403))

    with pytest.raises(RuntimeError) as excinfo:
        api.status(MEDIA_KEY)
    assert http_status(excinfo.value) == 403
    assert client.get.call_count == 2


CDN = "https://prod-fastly-us-east-1.video.pscp.tv"
VIDEO_DYN = (
    f"{CDN}/Transcoding/v1/hls/ID/non_transcode/us-east-1/pub/"
    "master_dynamic_7.m3u8?type=replay"
)
VIDEO_MASTER = "\n".join(
    [
        "#EXTM3U",
        "#EXT-X-DYNAMICALLY-GENERATED",
        "#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=1200000,RESOLUTION=854x480",
        "/Transcoding/v1/hls/ID/transcode/us-east-1/pub/J480/playlist_7.m3u8",
        "#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=300000,RESOLUTION=400x224",
        "/Transcoding/v1/hls/ID/transcode/us-east-1/pub/J224/playlist_7.m3u8",
    ]
)
VIDEO_PLAYLIST = "#EXTM3U\n#EXTINF:3.0,\nchunk_1_0_a.ts\n#EXTINF:3.0,\nchunk_1_1_a.ts"
SMALLEST = f"{CDN}/Transcoding/v1/hls/ID/transcode/us-east-1/pub/J224/playlist_7.m3u8"
AUDIO_DYN = (
    f"{CDN}/Transcoding/v1/hls/ID/non_transcode/eu-west-3/pub/"
    "audio-space/playlist_9.m3u8?type=replay"
)
AUDIO_MASTER = (
    f"{CDN}/Transcoding/v1/hls/ID/non_transcode/eu-west-3/pub/"
    "audio-space/master_playlist.m3u8"
)
AUDIO_RENDITION = "/Transcoding/v1/hls/ID/transcode/eu-west-3/pub/J/audio-space/p.m3u8"


class _Space(dict):
    def format(self, format_str):
        return format_str % self


def _downloader(dyn_url, pages, monkeypatch):
    space = _Space(
        id="1DGLdZnjOdPGm",
        state="Ended",
        available_for_replay=True,
        media_key="28_1",
        title="t",
        creator_name="c",
    )
    dl = TwspaceDL(space, "%(id)s")  # type: ignore[arg-type]
    dl.dyn_url = dyn_url  # skip the stream lookup
    client = MagicMock()
    client.get.side_effect = lambda url, **_: MagicMock(text=pages[url])
    monkeypatch.setattr(API, "client", client)
    return dl


def test_video_space_uses_the_smallest_rendition(monkeypatch):
    pages = {VIDEO_DYN: VIDEO_MASTER, SMALLEST: VIDEO_PLAYLIST}
    dl = _downloader(VIDEO_DYN, pages, monkeypatch)

    assert dl.is_video_space
    assert dl.playlist_url == SMALLEST


def test_video_space_chunks_resolve_against_the_rendition_playlist(monkeypatch):
    pages = {VIDEO_DYN: VIDEO_MASTER, SMALLEST: VIDEO_PLAYLIST}
    dl = _downloader(VIDEO_DYN, pages, monkeypatch)

    lines = dl.playlist_text.splitlines()
    chunks = [line for line in lines if not line.startswith("#")]
    base = f"{CDN}/Transcoding/v1/hls/ID/transcode/us-east-1/pub/J224/"
    assert chunks == [base + "chunk_1_0_a.ts", base + "chunk_1_1_a.ts"]


def test_audio_space_resolution_is_unchanged(monkeypatch):
    pages = {
        AUDIO_MASTER: f"#EXTM3U\n#X\n#EXT-X-STREAM-INF:BANDWIDTH=1\n{AUDIO_RENDITION}",
        CDN + AUDIO_RENDITION: "#EXTM3U\n#EXTINF:3.0,\nchunk_2_0_a.aac",
    }
    dl = _downloader(AUDIO_DYN, pages, monkeypatch)

    assert not dl.is_video_space
    assert dl.master_url == AUDIO_MASTER
    expected = AUDIO_MASTER.replace("master_playlist.m3u8", "chunk_2_0_a.aac")
    assert dl.playlist_text.splitlines()[-1] == expected


@pytest.mark.parametrize(
    "dyn_url, drops_video", [(VIDEO_DYN, True), (AUDIO_DYN, False)]
)
def test_download_drops_video_only_for_video_spaces(
    dyn_url, drops_video, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    dl = _downloader(dyn_url, {}, monkeypatch)
    monkeypatch.setattr(TwspaceDL, "write_playlist", lambda self, save_dir: None)
    monkeypatch.setattr(twspace_dl_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(twspace_dl_module.shutil, "move", lambda *_: None)
    run = MagicMock()
    monkeypatch.setattr(twspace_dl_module.subprocess, "run", run)

    dl.download()

    cmd = run.call_args.args[0]
    assert ("-vn" in cmd) is drops_video
    assert cmd[-1].endswith(".m4a")
