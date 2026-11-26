set -euo pipefail
umask 077
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
get_octopusvariable 'Papyrus.DispatchKey' | base64 -d > "$work/key"
get_octopusvariable 'Papyrus.HostKey' > "$work/known_hosts"
host=$(get_octopusvariable 'Papyrus.Host')
number=$(get_octopusvariable 'Octopus.Release.Number')
stage=STAGE_NAME
command=$stage
if [[ "$stage" != probe ]]; then
    [[ "$number" =~ ^[0-9]+\.[0-9]+\.[0-9]+\+[1-9][0-9]*$ ]]
    command="$stage $number"
fi
ssh -T -i "$work/key" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$work/known_hosts" \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=4 -- "root@$host" "$command"
