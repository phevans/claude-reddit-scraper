import pytest


@pytest.fixture
def sample_post_html():
    """A representative chunk of a New Music Monday post with two subgenres."""
    return """
    <hr>
    <h1>New Releases</h1>
    <h3>Dancefloor</h3>
    <ul>
        <li><p>Acelin, Maddy Lucas - Home Alone <em>[YosH]</em> | <a href="https://www.beatport.com/release/home-alone/5898264"><strong>[Beatport]</strong></a></p></li>
        <li><p>Hoax, Breach - Jack (Hoax Rework) <em>[Hospital]</em> | <a href="https://www.beatport.com/release/jack/5878031"><strong>[Beatport]</strong></a>, <a href="https://hoaxdnb.bandcamp.com/album/jack"><strong>[Bandcamp]</strong></a>, <a href="https://open.spotify.com/track/0Uekv0MjYK8cnsWDvQ3fld"><strong>[Spotify]</strong></a></p></li>
        <li><p>Chase &amp; Status, Pozer - Through The Pain <em>[Warner]</em> | <a href="https://www.beatport.com/release/through-the-pain/6411427"><strong>[Beatport]</strong></a>, <a href="https://open.spotify.com/album/10jaP6iO7YACKWJrrChMm2"><strong>[Spotify]</strong></a></p></li>
    </ul>
    <h3>Liquid</h3>
    <ul>
        <li><p>A.G - PLANETS <em>[Crate Classics]</em> | <a href="https://www.beatport.com/release/planets/5943507"><strong>[Beatport]</strong></a>, <a href="https://crateclassics.bandcamp.com/track/planets"><strong>[Bandcamp]</strong></a>, <a href="https://open.spotify.com/album/7BSEgMsCPqN6qCduDgCiEc"><strong>[Spotify]</strong></a></p></li>
        <li><p>Sebass - our summerlove <em>[Selfreleased]</em> | , <a href="https://open.spotify.com/album/4nzZW0RihHUoVWykfodECu"><strong>[Spotify]</strong></a></p></li>
    </ul>
    """


@pytest.fixture
def sample_post_html_no_releases():
    """HTML with no New Releases section."""
    return """
    <hr>
    <h1>Links &amp; Playlists</h1>
    <p>Some content here</p>
    """
