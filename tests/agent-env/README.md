# tests/agent-env — the single source that prepares the five agent test environments

This directory owns **test-environment preparation** for the five agent hosts used by the
gates: `hermes`, `openclaw`, `dsh`, `pi`, `deerflow`. It exists so the preparation is done
**once, here**, and reused by every gate (CLI L2 in the docker-host layer, L3 on runners)
instead of being re-invented — and re-broken — per run.

## Contract

An environment produced by `prepare.sh` must satisfy all of:

1. **Official installation method.** The agent is installed the way its upstream publishes
   it (hermes: the official `install.sh` source tree + venv; dsh/pi: the official npm
   package; openclaw: the official image; deerflow: the source-rebuilt image). Wheels or
   snapshots standing in for the official layout do not qualify.
2. **Base environment installed AND configured.** Interpreter, package manager and LLM
   configuration are in place before the test starts — never patched up mid-run.
3. **The agent runs and its LLM answers.** The agent's own entry point executes, and a live
   LLM probe returns content. If the endpoint is unreachable the check is reported as
   `SKIP`, **never** as a pass.
4. **Nothing of ours is pre-installed.** The aimail CLI, the pysdk (`agent-aimail`) and the
   tssdk (`@aimail/*`) must be absent; installing them is the thing under test.
5. **No package-manager release-age gate is left in the environment.**
   * python/uv: `<tree>/uv.toml` with a far-future cutoff (`exclude-newer`), which every uv
     version understands (the hermes base's uv 0.11.6 rejects the newer `false` form)
   * node/pnpm: `minimumReleaseAge = 0`. In the images this is carried by
     `PNPM_CONFIG_MINIMUM_RELEASE_AGE=0` (the value is pnpm's own built-in default and does
     not yield to a config file there), which later execs — including the product's own
     plugin install — inherit.

   Why this is a contract item: uv discovers config by walking **up from the CWD**, and the
   official hermes tree pins `[tool.uv] exclude-newer = "14 days"` in its own
   `pyproject.toml`; pnpm applies a release-age gate of its own (measured: openclaw
   `minimumReleaseAge=10080`). Under such a gate the *latest* version an installer can see
   is not the registry latest — the install "succeeds" and ships a **stale** package. That
   is a property of the environment, so it is removed here, once, and asserted afterwards
   both structurally and functionally (a dry-run from inside the tree must resolve the
   registry latest). The override uses a far-future timestamp rather than the newer
   `exclude-newer = false` form, because the hermes base image ships a uv (0.11.6) that
   rejects the boolean outright; verify.sh reds on a parse error even offline, so an
   environment whose uv cannot express the override is never accepted.
6. **Accepted offline, fail-closed.** `verify.sh --offline` is what `docker/build.sh` runs
   at build time; an environment that cannot pass it must not be published as an image.
7. **Pinned versions — the start point is reproducible.** `pins.env` is the single source
   for every version this environment is built from: base images are pinned as
   `<repo>:<tag>@<digest>` (a rebuild cannot silently pick up a newer base), and agents we
   install (dsh / pi / pnpm via npm, hermes on hosts via the official installer) are pinned
   to exact versions / commits. `verify.sh` asserts what actually landed and reds on drift.

   Why this is a contract item: a test that starts from a different environment than the run
   before it cannot be compared with it. "Is the environment the latest?" is a *maintenance*
   question — `update-pins.sh` re-resolves the current latest from authoritative sources
   (registry digest API, npm registry, the commit the official installer tracks) and rewrites
   `pins.env`; it is never called from a gate. Note the deliberate exception: some registry
   paths (mirrors) cannot serve a digest ref at all, and `docker/build.sh` then falls back to
   the tag **loudly** — it says the base may differ from the pin instead of pretending.

## Layout

```
agent-env/
  README.md            this contract
  pins.env             the pinned versions the tests run against (single source)
  update-pins.sh       move the pins to the current latest (maintenance only, no gates)
  prepare.sh <agent>   official install + base env + gates off + LLM config (idempotent)
  verify.sh  <agent>   the contract checks above (exit 0 pass / 1 red / 2 cannot judge)
  lib/llm-config.py    the ONE LLM-config writer/verifier for all five agents
  docker/Dockerfile.<agent>  the five host images (build the environment, nothing else)
  docker/build.sh            build + tag aimail-host-<agent>[:native] (fail-closed)
```

## Usage

```sh
# image world (docker hosts used by the CLI gate ladder)
bash tests/agent-env/docker/build.sh                 # all five
bash tests/agent-env/docker/build.sh hermes dsh      # a subset
bash tests/agent-env/docker/build.sh --assets <dir>  # add the harness regression scripts

# pinned versions: inspect what the current latest is, then bump deliberately
bash tests/agent-env/update-pins.sh --dry-run
bash tests/agent-env/update-pins.sh              # rewrites pins.env; rebuild + re-accept after

# host world (L3 runners): prepare, then judge
bash tests/agent-env/prepare.sh hermes --home "$HOME/.hermes" \
     --llm-url "$LLM_BASE_URL" --llm-model "$LLM_MODEL" --llm-key "$LLM_API_KEY" --extra-home "$HOME/.hermes"
bash tests/agent-env/verify.sh hermes --home "$HOME/.hermes" --llm-url ... --llm-model ...
```

`prepare.sh` ends by running `verify.sh` (use `--no-verify` to skip, e.g. during image
build, where `verify.sh --offline` is run as its own layer).

## Consumers

* **L3** (`tests/l3/run-l3.sh`, GitHub runners): calls `prepare.sh` for hermes and dsh
  before fetching the published CLI + gateway.
* **L2** (`aimail-advanced/tests/SDK/docker-regression/hosts/build-hosts.sh`): now a thin
  delegator that calls `docker/build.sh --assets <hosts dir>`, so the harness regression
  scripts still land in `/opt/regression` and the one `llm-config.py` is placed at
  `/opt/regression/llm-config.py` for the in-container scripts.

Retired with this: `tests/l3/llm-config.py`, the five host Dockerfiles and the
`llm-config.py` copy in the advanced repo (the four host-side references there resolve the
single source).

## Not this directory's job

* Installing the aimail CLI / pysdk / tssdk (contract item 4) — that is the product path
  under test.
* Gate orchestration, port allocation, fixtures, assertions about aimail behaviour — those
  stay with the gates that own them.
* Product-side hygiene of the install steps themselves. An environment is prepared clean;
  whether the product protects itself from a hostile CWD is a product question, tracked
  separately.
