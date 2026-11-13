import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from server import create_scorm12_bundle, create_share_bundle


video_id = "0123456789abcdef0123456789abcdef"
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    media_dir = root / "media-source"
    media_dir.mkdir()
    (media_dir / f"{video_id}.webm").write_bytes(b"webm-test-payload")
    output = root / "portable.zip"
    html = '<!doctype html><html><body><video src="media/' + video_id + '.webm"><track src="captions/step-1-en.vtt"></video></body></html>'
    create_share_bundle(html, [{
        "videoId": video_id,
        "stepNumber": 1,
        "captionTracks": [{"language": "en", "cues": [{"start": 0.125, "end": 1.5, "text": "Open <Settings>"}]}],
    }], media_dir, output)
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"index.html", f"media/{video_id}.webm", "captions/step-1-en.vtt"}
        assert archive.read(f"media/{video_id}.webm") == b"webm-test-payload"
        caption_file = archive.read("captions/step-1-en.vtt").decode("utf-8")
        assert "00:00:00.125 --> 00:00:01.500" in caption_file
        assert "Open &lt;Settings&gt;" in caption_file

    scorm_output = root / "course.zip"
    create_scorm12_bundle(html, [{
        "videoId": video_id,
        "stepNumber": 1,
        "captionTracks": [{"language": "en", "cues": [{"start": 0.125, "end": 1.5, "text": "Open Settings"}]}],
    }], media_dir, scorm_output, "Billing & account guide")
    with zipfile.ZipFile(scorm_output) as archive:
        names = set(archive.namelist())
        assert names == {"imsmanifest.xml", "index.html", f"media/{video_id}.webm", "captions/step-1-en.vtt"}
        root_xml = ET.fromstring(archive.read("imsmanifest.xml"))
        ns = {"imscp": "http://www.imsproject.org/xsd/imscp_rootv1p1p2", "adlcp": "http://www.adlnet.org/xsd/adlcp_rootv1p2"}
        assert root_xml.tag == "{http://www.imsproject.org/xsd/imscp_rootv1p1p2}manifest"
        assert "Billing & account guide" in root_xml.findtext(".//imscp:title", namespaces=ns)
        resource = root_xml.find(".//imscp:resource", namespaces=ns)
        assert resource.attrib["{http://www.adlnet.org/xsd/adlcp_rootv1p2}scormtype"] == "sco"
        declared = {entry.attrib["href"] for entry in resource.findall("imscp:file", namespaces=ns)}
        assert declared == {"index.html", f"media/{video_id}.webm", "captions/step-1-en.vtt"}
        scorm_html = archive.read("index.html").decode("utf-8")
        assert "LMSInitialize" in scorm_html and "LMSSetValue(\"cmi.core.lesson_status\",\"completed\")" in scorm_html
        assert "Mark tutorial complete" in scorm_html

    try:
        create_share_bundle("<!doctype html><script>alert(1)</script>", [], media_dir, root / "unsafe.zip")
    except ValueError:
        pass
    else:
        raise AssertionError("active script HTML should not be packaged")

print("portable share bundle: PASS")
