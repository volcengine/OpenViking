# Linux CI base images

`ubuntu20-ci.Dockerfile` provides GCC/G++ 10, ccache and the OS development
libraries needed by Linux wheels while keeping the Ubuntu 20.04 / glibc 2.31
baseline. `ubuntu24-ci.Dockerfile` provides the API test environment.
Python versions, Rust and project dependencies are installed by the existing
workflows.

To build and validate an image, run **Build Linux CI Base Image**
(`ci-linux-image.yml`) with `ubuntu_version=20.04` or `24.04`. Validation runs
without network access. Set `publish=true` to push the validated image to GHCR.
The workflow summary prints the immutable image reference.

After making the package publicly readable, set the matching repository
variable to that reference:

| Baseline | Repository variable | Consumers |
| --- | --- | --- |
| Ubuntu 20.04 | `CI_UBUNTU20_IMAGE` | Linux x86_64 wheel builds |
| Ubuntu 24.04 | `CI_UBUNTU24_IMAGE` | Native API runtime and test containers |

The Ubuntu 20.04 image is built for amd64. ARM wheel jobs continue to use
`ubuntu:20.04` and install their dependencies on the native ARM runner.
When a variable is unset, its jobs fall back to the corresponding Ubuntu base
image. Wheel jobs skip apt only when all required packages are installed, and
reject an image with a different glibc version before compiling.

When changing the wheel system-package list in `_build.yml`, update
`ubuntu20-ci.Dockerfile` to match, rebuild and publish the validated image, then
update `CI_UBUNTU20_IMAGE` to its new immutable digest. Apply the same process to
Ubuntu 24.04 when changing its consumers' system dependencies. An older image
still works through apt fallback, but wheel logs report the missing packages and
the build loses the preinstalled-image speedup until the variable is updated.
