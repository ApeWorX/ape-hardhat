
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
