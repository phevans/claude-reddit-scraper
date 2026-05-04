from __future__ import annotations

from bs4 import BeautifulSoup

from models import Release, SubgenreSection


def parse_releases(html: str) -> list[SubgenreSection]:
    """Parse the 'New Releases' section of a New Music Monday post into structured data."""
    soup = BeautifulSoup(html, "html.parser")

    # Find the "New Releases" heading (any heading level)
    new_releases_heading = None
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if "New Releases" in tag.get_text():
            new_releases_heading = tag
            break

    if not new_releases_heading:
        return []

    # Collect all elements after the "New Releases" heading in document order
    sections = []
    current_subgenre = None
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}

    for element in new_releases_heading.find_all_next():
        if element.name in heading_tags and element != new_releases_heading:
            current_subgenre = SubgenreSection(name=element.get_text().strip())
            sections.append(current_subgenre)
        elif element.name == "li" and current_subgenre is not None:
            # Only process <li> that are direct children of a <ul> (not nested)
            if element.parent and element.parent.name == "ul":
                release = _parse_release_item(element)
                if release:
                    current_subgenre.releases.append(release)

    return sections


def _parse_release_item(li) -> Release | None:
    """Parse a single <li> element into a Release."""
    # Extract label from <em> tag (format: [Label])
    em_tag = li.find("em")
    if not em_tag:
        return None
    label = em_tag.get_text().strip().strip("[]")

    # Extract links from <a> tags that contain <strong> text
    links = {}
    for a_tag in li.find_all("a"):
        strong = a_tag.find("strong")
        if strong:
            service_name = strong.get_text().strip().strip("[]")
            url = a_tag.get("href", "")
            if service_name and url:
                links[service_name] = url

    # Extract artist and title from the text before the <em> tag
    # The format is: "Artist1, Artist2 - Track Title [Label] | ..."
    # We need the text content before the <em> tag
    full_text = li.get_text()
    em_text = em_tag.get_text()

    # Get text before the label
    text_before_label = full_text.split(em_text)[0].strip()

    # Split on " - " to separate artists from title
    # Use first occurrence only, as title may contain " - "
    parts = text_before_label.split(" - ", 1)
    if len(parts) < 2:
        return None

    artists = parts[0].strip()
    title = parts[1].strip()

    return Release(artists=artists, title=title, label=label, links=links)
