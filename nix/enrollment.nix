{ ... }: {
  flake.lib.trustedDevicesFromHosts =
    hosts:
    let
      entries = builtins.map (
        host:
        let
          agent = host.config.services.family-controls.agent;
        in
        if
          !agent.enable
          || agent.credentialFile == null
          || agent.credentialHash == null
          || agent.accounts == { }
        then
          throw "trustedDevicesFromHosts requires enabled agents with credentialFile, credentialHash and accounts"
        else
          {
            name = agent.deviceId;
            value = {
              inherit (agent) credentialHash;
              accounts = builtins.mapAttrs (_: account: account.child) agent.accounts;
            };
          }
      ) (builtins.attrValues hosts);
      devices = builtins.listToAttrs entries;
    in
    if builtins.length entries != builtins.length (builtins.attrNames devices) then
      throw "trustedDevicesFromHosts requires unique device identities"
    else
      devices;
}
