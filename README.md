# ⚓ Docksmith

A simplified Docker-like build and runtime system built from scratch in Python.  
No daemon. No Docker. Just Linux namespaces, content-addressed layers, and a clean CLI + Web UI.

```
  ____             _            _ _   _     
 |  _ \  ___   ___| | ___ _ __ (_) |_| |__  
 | | | |/ _ \ / __| |/ __| '_ \| | __| '_ \ 
 | |_| | (_) | (__| | (__| | | | | |_| | | |
 |____/ \___/ \___|_|\___|_| |_|_|\__|_| |_|
```

---

## Architecture

```
docksmith (CLI)
│
├── docksmith/
│   ├── cli.py              ← argparse entry point
│   ├── build_engine.py     ← Docksmithfile parser + layer executor
│   ├── parser.py           ← Docksmithfile instruction parser
│   ├── layer_builder.py    ← reproducible tar delta layers
│   ├── cache_manager.py    ← deterministic cache key computation
│   ├── isolation.py        ← Linux namespace isolation (unshare + chroot)
│   ├── container_runtime.py ← assembles rootfs + runs containers
│   ├── image_store.py      ← manifest read/write/list/delete
│   └── state.py            ← ~/.docksmith/ directory management
│
├── scripts/
│   └── import_base_image.py ← one-time base image import
│
├── sample-app/
│   ├── Docksmithfile       ← uses all 6 instructions
│   ├── app.py              ← sample Python application
│   └── data/               ← bundled data files
│
├── ui/
│   └── index.html          ← single-file Web UI dashboard
│
├── ui_server.py            ← lightweight HTTP server for the UI
└── setup.sh                ← one-time setup script
```

### State Directory (`~/.docksmith/`)

```
~/.docksmith/
├── images/          # one JSON manifest per image
│   └── myapp_latest.json
├── layers/          # content-addressed tar files, named by SHA-256 digest
│   ├── a3f9b2c1….tar
│   └── d8e4a1f0….tar
└── cache/
    └── index.json   # cache key → layer digest mapping
```

---

## Requirements

- **Linux** (WSL2 fully supported)
- Python 3.8+
- `unshare` and `chroot` (from `util-linux`, pre-installed on most systems)
- User namespaces enabled (default on most modern Linux/WSL2)

---

## Installation

```bash
# Clone / extract the project, then:
bash setup.sh
```

This will:
1. Verify Python 3, `unshare`, and `chroot` are available
2. Test that user namespaces work
3. Install the `docksmith` CLI to `~/.local/bin/`
4. Import base images into `~/.docksmith/`

Add to PATH if needed:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

## CLI Reference

### `docksmith build -t <name:tag> <context> [--no-cache]`

Parse the `Docksmithfile` in `<context>`, execute all steps, write the image manifest.

```
Step 1/4 : FROM python:3.12-slim
Step 2/4 : WORKDIR /app
Step 3/4 : COPY app.py /app/app.py [CACHE MISS] 0.09s
Step 4/4 : RUN echo "Build complete" [CACHE MISS] 0.14s

Successfully built sha256:a3f9b2c1d8e4 myapp:latest (0.23s)
```

### `docksmith images`

```
NAME                 TAG        ID               CREATED
────────────────────────────────────────────────────────────────────────
alpine               3.18       d2f3a8c9e0b1     2025-01-15T10:30:00Z
python               3.12-slim  a1b2c3d4e5f6     2025-01-15T10:30:01Z
myapp                latest     a3f9b2c1d8e4     2025-01-15T10:31:00Z
```

### `docksmith run <name:tag> [cmd] [-e KEY=VALUE]`

```bash
docksmith run myapp:latest
docksmith run myapp:latest /bin/sh
docksmith run -e GREETING=Ahoy myapp:latest
```

### `docksmith rmi <name:tag>`

Removes the image manifest and all associated layer tar files.

---

## Docksmithfile Reference

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV APP_NAME=MyApp
ENV VERSION=1.0

COPY app.py /app/app.py
COPY data/ /app/data/

RUN echo "Building $APP_NAME v$VERSION"

CMD ["/usr/bin/python3", "/app/app.py"]
```

| Instruction | Produces Layer? | Description |
|---|---|---|
| `FROM <image>[:<tag>]` | No | Load base image layers |
| `WORKDIR <path>` | No | Set working directory for subsequent instructions |
| `ENV <key>=<value>` | No | Set environment variable (injected into RUN and containers) |
| `COPY <src...> <dest>` | **Yes** | Copy files from build context into image |
| `RUN <command>` | **Yes** | Execute command inside image filesystem (isolated) |
| `CMD ["exec","arg"]` | No | Default command when running the container |

---

## Build Cache

Cache keys are computed from:
- Digest of the previous layer (or base image manifest digest for the first step)
- Full instruction text
- Current `WORKDIR` value
- All accumulated `ENV` values (sorted by key)
- For `COPY`: SHA-256 of each source file (sorted by path)

Any change cascades all downstream steps to cache misses.

```bash
# Cold build (all misses)
docksmith build -t myapp:latest ./sample-app

# Warm build (all hits, near-instant)
docksmith build -t myapp:latest ./sample-app

# Force rebuild
docksmith build --no-cache -t myapp:latest ./sample-app
```

---

## Isolation

Container processes are isolated using:
- **Mount namespace** (`unshare --mount`): mounts are invisible to the host
- **UTS namespace** (`unshare --uts`): hostname is isolated
- **User namespace** (`--map-root-user`): runs as mapped-root without real root privileges
- **chroot**: changes visible root filesystem to the assembled image layers

The **same isolation primitive** is used for both `RUN` during build and `docksmith run`.

**Isolation guarantee**: A file written inside a container will NOT appear on the host filesystem after the container exits. The temporary rootfs is always cleaned up.

---

## Web UI

```bash
python3 ui_server.py
# Open: http://localhost:7474
```

Features:
- Browse all local images with metadata
- Start builds with live log streaming
- Run containers and see output
- Inspect image layers and config
- Delete images
- Import base images
- Cache and store statistics

---

## Demo Script

```bash
# 1. Cold build — all [CACHE MISS]
docksmith build -t myapp:latest ./sample-app

# 2. Warm build — all [CACHE HIT], near-instant
docksmith build -t myapp:latest ./sample-app

# 3. Edit a source file, rebuild — partial cache
echo "# edited" >> sample-app/app.py
docksmith build -t myapp:latest ./sample-app

# 4. List images
docksmith images

# 5. Run the container
docksmith run myapp:latest

# 6. Override environment variable
docksmith run -e GREETING=Ahoy myapp:latest

# 7. Isolation test — write a file inside, verify it's not on host
docksmith run myapp:latest
# The app writes /tmp/isolation_test.txt inside the container
ls /tmp/isolation_test.txt   # → No such file or directory ✓

# 8. Remove image
docksmith rmi myapp:latest
```

---

## Reproducible Builds

- Tar entries are added in **sorted order** (both files and directories)
- File **timestamps are zeroed** (`mtime=0`) in all layer tars
- ENV state is serialized in **lexicographically sorted key order**
- Cache keys are deterministic SHA-256 hashes

The same `Docksmithfile` and source files produce **identical layer digests** on every build.

---

## Constraints Met

| Requirement | Status |
|---|---|
| No Docker / runc / containerd | ✓ Pure Python + Linux syscalls |
| RUN executes inside image filesystem | ✓ unshare + chroot |
| Same isolation for RUN and run | ✓ `isolation.py` used in both |
| Verified isolation (no host leakage) | ✓ rootfs deleted after exit |
| No network during build/run | ✓ Base images pre-imported |
| Content-addressed layers | ✓ SHA-256 of tar bytes |
| Reproducible builds | ✓ Sorted entries, zeroed timestamps |
| All 6 instructions implemented | ✓ FROM COPY RUN WORKDIR ENV CMD |
| Build cache with cascade | ✓ CacheManager with full invalidation rules |
| Manifest digest computation | ✓ SHA-256 of canonical JSON with digest="" |

---

## License

MIT
