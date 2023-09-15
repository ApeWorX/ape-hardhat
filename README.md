# Quick Start

This is a Hardhat network provider plugin for Ape.
Hardhat is a development framework written in Node.js for Ethereum that includes a local network implementation.
Use this plugin to manage a Hardhat node process or connect to an existing one.

## Dependencies

- [python3](https://www.python.org/downloads) version 3.8 up to 3.11.
- Node.js, NPM, and Hardhat 2.12.0 or greater. See Hardhat's [Installation](https://hardhat.org/getting-started/#installation%3E) documentation for steps.

## Installation

### via `pip`

You can install the latest release via [`pip`](https://pypi.org/project/pip/):

```bash
pip install ape-hardhat
```

### via `setuptools`

You can clone the repository and use [`setuptools`](https://github.com/pypa/setuptools) for the most up-to-date version:

```bash
git clone https://github.com/ApeWorX/ape-hardhat.git
cd ape-hardhat
python3 setup.py install
```

## Quick Usage

To use the plugin, first install Hardhat locally into your Ape project directory:

```bash
cd your-ape-project
npm install --save-dev hardhat
```

After that, you can use the `--network ethereum:local:hardhat` command line flag to use the hardhat network (if it's not already configured as the default).

This network provider takes additional Hardhat-specific configuration options. To use them, add these configs in your project's `ape-config.yaml`:

```yaml
hardhat:
  host: 127.0.0.1:8555
```

To select a random port, use a value of "auto":

```yaml
hardhat:
  host: auto
```

**NOTE**: If you plan on running multiple Hardhat nodes of any kind, you likely will want to use `auto`.

This is useful for multiprocessing and starting up multiple providers.

You can also adjust the request timeout setting:

```yaml
hardhat:
  request_timeout: 20  # Defaults to 30
  fork_request_timeout: 600  # Defaults to 300
```

## Mainnet Fork

The `ape-hardhat` plugin also includes a mainnet fork provider. It requires using another provider that has access to mainnet.

Use it in most commands like this:

```bash
ape console --network :mainnet-fork:hardhat
```

Specify the upstream archive-data provider in your `ape-config.yaml`:

```yaml
hardhat:
  fork:
    ethereum:
      mainnet:
        upstream_provider: alchemy
```

Otherwise, it defaults to the default mainnet provider plugin. You can also specify a `block_number`.

**NOTE**: Make sure you have the upstream provider plugin installed for ape.

[Hardhat deployments](https://github.com/wighawag/hardhat-deploy#deploy-scripts-tags-and-dependencies) are disabled for forks for performance reasons. If you want your contract deployments to run on your fork, you can set `enable_hardhat_deployments` to `true` in your config:

```yaml
hardhat:
  fork:
    ethereum:
      mainnet:
        upstream_provider: alchemy
        enable_hardhat_deployments: true
```

```bash
ape plugins install alchemy
```

## Remote Hardhat Node

To connect to a Hardhat node, set up your config like this:

```yaml
hardhat:
  host: https://hardhat.example.com
```

Now, instead of launching a local process, it will attempt to connect to the remote Hardhat node and use this plugin as the ape interace.

## Custom Hardhat Config File

By default, Ape generates and uses a basic config file for starting up a Hardhat node and having the same test accounts that Ape expects.
To avoid conflict with other pre-existing Hardhat config files, Ape generates one in `$HOME/.ape/hardhat` and always refers to that one.
To use a different one, such as the one in your local project instead, add the following to your `ape-config.yaml`:

```yaml
hardhat:
  hardhat_config_file: ./hardhat.config.ts
```

**NOTE**: You can refer to either a Hardhat JS file or a Hardhat TS file.

## Development

Please see the [contributing guide](CONTRIBUTING.md) to learn more how to contribute to this project.
Comments, questions, criticisms and pull requests are welcomed.
