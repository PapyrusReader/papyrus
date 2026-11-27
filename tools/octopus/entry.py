"""Accept only the fixed release operations authorized for the Octopus SSH key."""
import json
import sys

from pipeline import ROOT, STAGES, GitHub, main, parse_release


def dispatch(command):
    if command == "probe":
        hub = GitHub(ROOT)
        revision = hub.call("api", "repos/PapyrusReader/papyrus/git/ref/heads/master", "--jq", ".object.sha")
        print(json.dumps({"workspace_master": revision, "stages": list(STAGES)}))
        return
    fields = command.split(" ")
    has_manifest = fields[0] == "prepare" and len(fields) == 3
    if fields[0] not in STAGES or (len(fields) != 2 and not has_manifest):
        raise ValueError("Expected a Papyrus release stage and version")
    parse_release(fields[1])
    main(*fields)


if __name__ == "__main__":
    dispatch(sys.argv[1])
