# Windows engineering acceptance helper

`tools/verify_windows_browser.py` uses a dedicated headless Chrome profile on an actual Windows machine. It verifies the shipped Godot Web layout at initial/trial/finished states, local recording while a real upload is held, the 64-event memory limit, 32-event upload batches and final cleanup. It records screenshots and synthetic evidence in its current directory. It never attaches to the normal Chrome profile and refuses an occupied test debugging port.

The maintainer prepares an explicitly selected evidence directory containing this script, `run_url.txt` and dependencies installed from `tools/windows_browser_requirements.txt` into `deps`. The URL must be an approved synthetic release at `http://experiment.localhost:8000/run/...`. For the tested setup, a temporary LAN SSH reverse tunnel bound **only Windows loopback** port 8000 to the existing guarded Mac service. This does not replace the earlier physical HTTPS/unplug acceptance evidence.

From that prepared directory:

```powershell
python -m pip install --no-deps --require-hashes --target deps -r requirements.txt
python verify_windows_browser.py
```

The maintainer independently compares report event IDs and values with the database and an authorized JSONL snapshot. Review `trial2.png` and `finished.png` for overlap. Keep reports/profiles outside Git; they are not participant data. The tool closes its own Chrome process and retains evidence profiles. Close the temporary SSH tunnel separately. Do not use a local Mac window as a substitute for the Windows machine, or count this automation as T17 independent human acceptance.
