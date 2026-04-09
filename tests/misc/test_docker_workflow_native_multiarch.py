from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_build_docker_workflow_uses_native_parallel_multiarch_jobs():
    workflow = _read_text(".github/workflows/build-docker-image.yml")

    assert "docker/setup-qemu-action" not in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "docker buildx imagetools create" in workflow
    assert "push-by-digest=true" in workflow
    assert "name-canonical=true" in workflow
    assert '"${tag}-amd64"' not in workflow
    assert '"${tag}-arm64"' not in workflow
    assert "platforms: linux/amd64,linux/arm64" not in workflow


def test_release_docker_workflow_uses_native_parallel_multiarch_jobs():
    workflow = _read_text(".github/workflows/release.yml")

    assert "docker/setup-qemu-action" not in workflow
    assert "ubuntu-24.04-arm" in workflow
    assert "docker buildx imagetools create" in workflow
    assert "push-by-digest=true" in workflow
    assert "name-canonical=true" in workflow
    assert '"${tag}-amd64"' not in workflow
    assert '"${tag}-arm64"' not in workflow
    assert "platforms: linux/amd64,linux/arm64" not in workflow


def test_build_docker_workflow_uses_manual_input_version_for_dispatch_tags():
    workflow = _read_text(".github/workflows/build-docker-image.yml")

    assert "type=raw,value=${{ github.event.inputs.version }}" in workflow
    assert "type=ref,event=tag" in workflow


def test_docker_workflows_normalize_image_names_to_lowercase():
    build_workflow = _read_text(".github/workflows/build-docker-image.yml")
    release_workflow = _read_text(".github/workflows/release.yml")

    assert "tr '[:upper:]' '[:lower:]'" in build_workflow
    assert "steps.image-name.outputs.image" in build_workflow
    assert "tr '[:upper:]' '[:lower:]'" in release_workflow
    assert "steps.image-name.outputs.image" in release_workflow


def test_build_docker_workflow_tracks_registry_specific_digests_for_manifests():
    workflow = _read_text(".github/workflows/build-docker-image.yml")

    assert "docker-digests-ghcr-${{ matrix.arch }}" in workflow
    assert "docker-digests-dockerhub-${{ matrix.arch }}" in workflow
    assert 'ghcr_digest="${{ steps.push-ghcr.outputs.digest }}"' in workflow
    assert 'dockerhub_digest="${{ steps.push-dockerhub.outputs.digest }}"' in workflow
    assert "pattern: docker-digests-ghcr-*" in workflow
    assert "pattern: docker-digests-dockerhub-*" in workflow
    assert (
        'ghcr_image_refs+=("${{ env.REGISTRY }}/${{ steps.image-name.outputs.image }}@${digest}")'
        in workflow
    )
    assert (
        'dockerhub_image_refs+=("docker.io/${{ secrets.DOCKERHUB_USERNAME }}/openviking@${digest}")'
        in workflow
    )


def test_release_workflow_tracks_registry_specific_digests_for_manifests():
    workflow = _read_text(".github/workflows/release.yml")

    assert "docker-digests-ghcr-${{ matrix.arch }}" in workflow
    assert "docker-digests-dockerhub-${{ matrix.arch }}" in workflow
    assert 'ghcr_digest="${{ steps.push-ghcr.outputs.digest }}"' in workflow
    assert 'dockerhub_digest="${{ steps.push-dockerhub.outputs.digest }}"' in workflow
    assert "pattern: docker-digests-ghcr-*" in workflow
    assert "pattern: docker-digests-dockerhub-*" in workflow
