"""Per-job Docker network creation and teardown."""

import docker
from docker.errors import NotFound


class NetworkManager:
    """Manages isolated Docker networks for each job."""

    @staticmethod
    def create(job_id: str) -> str:
        """Create an isolated Docker bridge network for a job.

        Args:
            job_id: The job UUID string.

        Returns:
            The network name (gitdeploy_net_{job_id[:8]}).
        """
        client = docker.from_env()
        network_name = f"gitdeploy_net_{job_id[:8]}"

        # Remove existing network with same name if it exists (leftover from crash)
        try:
            existing = client.networks.get(network_name)
            existing.remove()
        except NotFound:
            pass

        client.networks.create(
            name=network_name,
            driver="bridge",
            internal=False,  # Allow outbound internet for package installation
            labels={"gitdeploy_job_id": job_id},
        )
        return network_name

    @staticmethod
    def remove(network_name: str) -> None:
        """Remove a Docker network. Silently ignores if not found."""
        try:
            client = docker.from_env()
            network = client.networks.get(network_name)
            network.remove()
        except NotFound:
            pass
        except Exception:
            # Network may still have connected containers; force disconnect
            try:
                client = docker.from_env()
                network = client.networks.get(network_name)
                for container in network.attrs.get("Containers", {}).values():
                    try:
                        network.disconnect(container["Name"], force=True)
                    except Exception:
                        pass
                network.remove()
            except Exception:
                pass
