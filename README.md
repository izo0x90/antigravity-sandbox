```text
    ___   ________  __    _____                 ____                
   /   | / ____/\ \/ /   / ___/____ _____  ____/ / /_  ____  _  __
  / /| |/ / __   \  /    \__ \/ __ `/ __ \/ __  / __ \/ __ \| |/_/
 / ___ / /_/ /   / /    ___/ / /_/ / / / / /_/ / /_/ / /_/ />  <  
/_/  |_\____/   /_/    /____/\__,_/_/ /_/\__,_/_.___/\____/_/|_|  
```

# agy-sandbox

A lightweight CLI tool that runs Google Antigravity and your project inside an isolated, containerized Docker environment. It allows you to sandbox the Antigravity agent in a project, control runtime dependencies dynamically, and manage distinct Google logins securely.

## Prerequisites

- **Docker:** Must be installed and running on your host machine.
- **uv:** For installing the Python CLI tool globally.

## Installation

Install the CLI globally directly from this repository using `uv`:

```bash
uv tool install -e .
```

## Usage & Commands

- `agy-sandbox init`
  Generates a default `agy.yaml` configuration file in your project.

- `agy-sandbox auto-init` *(Optional)*
  Uses your host machine's `agy` engine to intelligently scan the project and generate an optimal `agy.yaml` tailored to your codebase.

- `agy-sandbox update-base`
  Pulls the latest Antigravity engine and CLI from Google and bakes them securely into the local `agy-base:latest` Docker image. Run this whenever you want to update the agent.

- `agy-sandbox up`
  Dynamically builds the project container and launches an interactive bash session with your workspace mounted.

- `agy-sandbox down`
  Forcefully stops and removes the running sandbox container for the current project.

## Configuration (`agy.yaml`)

The environment is declaratively configured via the `agy.yaml` file in the root of your project:

```yaml
profile: default_project
project_name: my_app
runtime:
  python: "3.11"
  node: "20"
  apt_packages:
    - build-essential
setup_scripts:
  - npm install
env:
  - ENVIRONMENT=development
use_native_login: false
```

- **`profile`**: Namespaces your Google login token. `profile: client_a` will isolate the agent's authentication to a `~/.gemini_client_a` folder on your host machine. This prevents cross-contamination of accounts.
- **`use_native_login`**: Set this to `true` to completely bypass the isolated profile and securely mount your host machine's active `~/.gemini` folder. This passes your current host session directly into the container.
- **`runtime`**: Dynamically injects dependencies like Python, Node.js, and system `apt` packages at boot.

## 🔐 Multiple Isolated Logins

One of the most powerful features of `agy-sandbox` is its ability to manage **completely separate, isolated Antigravity logins** for different projects. 

Instead of constantly running `agy login` and `agy logout` when switching between personal projects, freelance clients, or work repositories, `agy-sandbox` handles it automatically:
- Each project can define its own `profile` in `agy.yaml`.
- The sandbox automatically spins up a headless Linux keyring database specific to that profile.
- Authentication tokens are encrypted and persisted securely on your host machine under `~/.gemini_<profile>`.
- When you boot the sandbox via `agy-sandbox up`, you are automatically authenticated as the correct user for that specific project!

## Architecture

This tool uses a two-tier Docker architecture for maximum performance and isolation:
1. A static `Dockerfile.base` caches the heavy Google Antigravity engine, ensuring it doesn't need to be downloaded repeatedly.
2. A dynamically generated Dockerfile handles project-specific configurations (like Python and Node runtimes) to boot up quickly without polluting the base image.

## Contributing

We welcome contributions to make `agy-sandbox` even better!

- **Reporting Issues:** Please file any bugs or feature requests in the repository's issue tracker.
- **Development Setup:** 
  1. Clone this repository.
  2. Make your code changes in the `src/` directory.
  3. Test your changes locally by running `uv tool install -e .` to apply your local edits to your global installation.
- **Pull Request Process:** Fork the repository, create a feature branch (`git checkout -b feature/my-new-thing`), commit your changes, and submit a PR against the `main` branch.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
