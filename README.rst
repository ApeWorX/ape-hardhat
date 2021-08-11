ape-hardhat
###########

Hardhat network provider plugin for Ape. Hardhat is a development framework written in Node.js for Ethereum that includes a local network implementation.

Dependencies
************

* `python3 <https://www.python.org/downloads>`_ version 3.7 or greater, python3-dev
* Node.js, NPM, and Hardhat. See Hardhat's `Installation <https://hardhat.org/getting-started/#installation>`_ documentation for steps.

Installation
************

via ``pip``
===========

You can install the latest release via `pip <https://pypi.org/project/pip/>`_:

.. code-block:: bash

    pip install ape-hardhat

via ``setuptools``
==================

You can clone the repository and use `setuptools <https://github.com/pypa/setuptools>`_ for the most up-to-date version:

.. code-block:: bash

    git clone https://github.com/ApeWorX/ape-hardhat.git
    cd ape-hardhat
    python3 setup.py install

Quick Usage
***********

To use the plugin, first install Hardhat locally into your Ape project directory:

.. code-block:: bash

    cd your-ape-project
    npm install --save-dev hardhat

After that, you can use the ``--network ethereum:development:hardhat`` command line flag to use the hardhat network (if it's not already configured as the default).

This network provider takes additional Hardhat-specific configuration options. To use them, add these configs in your project's ``ape-config.yaml``:

.. code-block:: yaml

    hardhat:
      port: 8555
      ethereum:
        development:
          uri: http://localhost:8555

Development
***********

This project is in early development and should be considered an alpha.
Things might not work, breaking changes are likely.
Comments, questions, criticisms and pull requests are welcomed.

License
*******

This project is licensed under the `Apache 2.0 <./LICENSE>`_.
