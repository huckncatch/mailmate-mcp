"""Self-test proving AppleScript values are passed as argv, never interpolated."""

import sys


class FakeCompletedProcess:
    def __init__(self, argv):
        self.argv = argv
        self.returncode = 0
        self.stdout = ""
        self.stderr = ""


def fake_run(argv, capture_output=True, text=True):
    fake_run.calls.append(argv)
    return FakeCompletedProcess(argv)


fake_run.calls = []


if __name__ == "__main__":
    sys.path.insert(0, "src")
    from mailmate_mcp import server

    server.subprocess.run = fake_run

    bad = 'x") & (do shell script "touch /tmp/PWNED") & ("'

    server._run_applescript(
        'on run argv\n'
        'tell application "MailMate" to open location (item 1 of argv)\n'
        'end run',
        bad,
    )

    assert fake_run.calls, "subprocess.run was not called"
    recorded_argv = fake_run.calls[-1]

    assert bad in recorded_argv, "payload was not passed as its own argv element"

    script_arg = recorded_argv[2]
    assert bad not in script_arg, "payload leaked into the script string"

    print("OK")
