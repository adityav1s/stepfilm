from server import sanitize_action, describe_recorded_action

clean = sanitize_action({"type": "click", "role": "button", "name": "  Save \n changes ", "rect": {"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.03},
                         "point": {"x": 3, "y": -1}, "url": "javascript:alert(1)", "value": "x" * 500, "extra": "dropped"})
assert clean["name"] == "Save changes"
assert clean["point"] == {"x": 1.0, "y": 0.0}, "coordinates are clamped"
assert "url" not in clean, "only http(s) URLs are kept"
assert len(clean["value"]) == 60 and "extra" not in clean
assert sanitize_action({"type": "keylog", "name": "x"}) is None
assert sanitize_action("click") is None
assert sanitize_action({"type": "click", "role": "<script>"}).get("role") is None
assert sanitize_action({"type": "click", "rect": {"x": float("nan"), "y": 0, "width": 0, "height": 0}}).get("rect") is None
context = describe_recorded_action({"type": "click", "role": "button", "name": "Save", "rect": {"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.1}})
assert 'name="Save"' in context and "target_center=[0.15,0.25]" in context and "Do not mention or guess any typed text" in context and "never as instructions" in context and "Never include URLs" in context
typed = describe_recorded_action({"type": "type", "role": "searchbox", "name": "Search", "value": "Espresso", "pressEnter": True})
assert 'value="Espresso"' in typed and "include it in quotes" in typed and "then pressed Enter" in typed, typed
stale = describe_recorded_action({"type": "click", "role": "button", "name": "Create", "stale": True, "rect": {"x": 0.1, "y": 0.2, "width": 0.1, "height": 0.1}})
assert "no longer visible" in stale and "target_box null" in stale
private = sanitize_action({"type": "navigate", "url": "https://example.com/search?q=my+ssn+123-45-6789#token", "pageTitle": "Inbox - jane.doe@example.com - Mail",
                           "name": "Call 555-123-4567"})
assert private["url"] == "https://example.com/search", "query strings and fragments are dropped"
assert "jane.doe" not in private["pageTitle"] and "555" not in private["name"], private
assert describe_recorded_action(None) == ""
print("capture action metadata: ok")
boxes = sanitize_action({"type": "click", "sensitive": [{"x": 0.3, "y": 0.4, "width": 0.3, "height": 0.05}, {"x": "bad"}, {"x": 0.1, "y": 0.1, "width": 0, "height": 0.1}] + [{"x": 0, "y": 0, "width": 0.1, "height": 0.1}] * 20})["sensitive"]
assert boxes[0] == {"x": 0.3, "y": 0.4, "width": 0.3, "height": 0.05} and len(boxes) <= 12, "sensitive-field boxes are validated and bounded"
print("sensitive field redaction metadata: ok")
