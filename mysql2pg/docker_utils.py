"""
Docker client management and PostgreSQL container lifecycle.
"""

import sys
import time

import docker
from docker.errors import DockerException, NotFound, APIError

from mysql2pg import (
    console, CONFIG_FILE,
    DOCKER_NETWORK, PG_CONTAINER_NAME, PG_IMAGE,
)
from mysql2pg.config import PGConfig


def get_docker_client() -> docker.DockerClient:
    """Get Docker client, with a friendly error if Docker isn't running."""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except DockerException:
        console.print(
            "\n[red]✗ Cannot connect to Docker.[/red]\n"
            "  Make sure Docker Engine is installed and running:\n"
            "    [dim]sudo systemctl start docker[/dim]\n"
            "    [dim]sudo usermod -aG docker $USER[/dim]\n"
        )
        sys.exit(1)


def ensure_network(client: docker.DockerClient, name: str):
    """Create Docker network if it doesn't exist."""
    try:
        client.networks.get(name)
    except NotFound:
        try:
            client.networks.create(name, driver="bridge")
        except APIError as e:
            console.print(
                f"\n[red]✗ Failed to create Docker network '{name}':[/red] {e}\n"
                "  [dim]Try manually: docker network create " + name + "[/dim]\n"
            )
            sys.exit(1)


def start_postgres(client: docker.DockerClient, pg: PGConfig) -> docker.models.containers.Container:
    """Start PostgreSQL container, or reuse if already running."""

    # Remove legacy container name if exists (fixes pgloader underscore issue)
    if PG_CONTAINER_NAME != "pg_target":
        try:
            legacy = client.containers.get("pg_target")
            console.print("  [dim]Removing legacy container 'pg_target'...[/dim]")
            legacy.remove(force=True)
        except NotFound:
            pass
        except APIError:
            pass

    # Check if container already exists
    try:
        container = client.containers.get(PG_CONTAINER_NAME)
        if container.status == "running":
            console.print("  [green]✓[/green] PostgreSQL container already running")
            return container
        else:
            console.print(f"  [dim]Removing stopped container '{PG_CONTAINER_NAME}'...[/dim]")
            try:
                container.remove(force=True)
            except APIError as e:
                console.print(
                    f"\n[red]✗ Cannot remove old container '{PG_CONTAINER_NAME}':[/red] {e}\n"
                    f"  [dim]Try manually: docker rm -f {PG_CONTAINER_NAME}[/dim]\n"
                )
                sys.exit(1)
    except NotFound:
        pass

    # Pull image if needed
    try:
        client.images.get(PG_IMAGE)
    except NotFound:
        console.print(f"  Pulling [cyan]{PG_IMAGE}[/cyan]...")
        try:
            client.images.pull(PG_IMAGE)
        except APIError as e:
            console.print(
                f"\n[red]✗ Failed to pull Docker image {PG_IMAGE}:[/red] {e}\n"
                "  [yellow]Troubleshooting:[/yellow]\n"
                "    1. Check internet connection\n"
                "    2. Docker Hub may be down — check [dim]https://status.docker.com[/dim]\n"
                "    3. Try manually: [dim]docker pull " + PG_IMAGE + "[/dim]\n"
            )
            sys.exit(1)

    # Ensure network exists
    ensure_network(client, DOCKER_NETWORK)

    # Start container
    try:
        container = client.containers.run(
            PG_IMAGE,
            name=PG_CONTAINER_NAME,
            detach=True,
            ports={"5432/tcp": pg.port},
            environment={
                "POSTGRES_USER": pg.user,
                "POSTGRES_PASSWORD": pg.password,
                "POSTGRES_DB": pg.database,
            },
            volumes={"sql_pgdata": {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
            network=DOCKER_NETWORK,
            restart_policy={"Name": "unless-stopped"},
            healthcheck={
                "test": ["CMD-SHELL", f"pg_isready -U {pg.user}"],
                "interval": 5_000_000_000,  # 5s in nanoseconds
                "timeout": 5_000_000_000,
                "retries": 5,
            },
        )
    except APIError as e:
        error_msg = str(e)
        if "port is already allocated" in error_msg or "address already in use" in error_msg:
            console.print(
                f"\n[red]✗ Port {pg.port} is already in use.[/red]\n"
                "  [yellow]Troubleshooting:[/yellow]\n"
                f"    1. Check what's using port {pg.port}: [dim]sudo lsof -i :{pg.port}[/dim]\n"
                f"    2. Stop the conflicting service, or\n"
                f"    3. Change [cyan]postgresql.port[/cyan] in {CONFIG_FILE.name} to a different port\n"
            )
        elif "Conflict" in error_msg:
            console.print(
                f"\n[red]✗ Container name '{PG_CONTAINER_NAME}' conflict:[/red] {e}\n"
                f"  [dim]Try: docker rm -f {PG_CONTAINER_NAME}[/dim]\n"
            )
        else:
            console.print(
                f"\n[red]✗ Failed to start PostgreSQL container:[/red] {e}\n"
                "  [dim]Check Docker logs: docker logs " + PG_CONTAINER_NAME + "[/dim]\n"
            )
        sys.exit(1)

    return container


def wait_for_postgres(client: docker.DockerClient, timeout: int = 60):
    """Wait until PostgreSQL container is healthy."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            container = client.containers.get(PG_CONTAINER_NAME)
            health = container.attrs.get("State", {}).get("Health", {}).get("Status", "")
            if health == "healthy":
                return True
            if container.status != "running":
                console.print(f"  [red]✗ Container exited unexpectedly[/red]")
                return False
        except NotFound:
            return False
        time.sleep(2)

    console.print(f"  [red]✗ PostgreSQL did not become healthy within {timeout}s[/red]")
    return False
