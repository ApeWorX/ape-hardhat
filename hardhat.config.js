
// See https://hardhat.org/config/ for config options.
module.exports = {
  networks: {
    hardhat: {
      hardfork: "london",
      // base fee of 0 allows use of 0 gas price when testing
      initialBaseFeePerGas: 0,
    },
  },
};

task("dump-accounts", "Dumps the accounts as JSON for the current project", async (taskArgs, hre) => {
  const util = require("hardhat/internal/core/providers/util");
  const { bufferToHex, privateToAddress, toBuffer } = require("ethereumjs-util");
  const accts = util.normalizeHardhatNetworkAccountsConfig(config.networks.hardhat.accounts);
  let out = [];
  for(let acct of accts) {
    acct.address = bufferToHex(privateToAddress(toBuffer(acct.privateKey)));
    out.push(acct);
  }
  console.log(JSON.stringify(out, null, 2));
});

