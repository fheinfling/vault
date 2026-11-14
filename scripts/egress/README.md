# Egress allowlist for the sync process

macOS's packet filter (`pf`) lets the sync reach only the providers' hosts on port 443. The rules match outbound sockets by their **effective group**: the installed `vault` binary is setgid to a dedicated group, also called `vault`, so a sync started from it carries that group while the rest of your login session does not. This is set up **before the first real credential** goes into the Keychain. Even code that turned malicious could then open connections to nothing but the listed hosts.

Why a group and not a separate user: the sync has to run as you, because the Keychain items and the database are yours and a launch agent runs in your login session. A user-scoped rule would therefore never match the sync process. A group-scoped rule does, as long as the binary carries the setgid bit.

## One-time setup

1. Create the group (any unused ID above 500 works):

   ```sh
   sudo dscl . -create /Groups/vault PrimaryGroupID 790
   sudo dscl . -create /Groups/vault RealName "vault egress group"
   ```
2. Install the binary setgid `vault`:

   ```sh
   swift build -c release
   sudo install -m 2755 -o root -g vault .build/release/vault /usr/local/bin/vault
   /usr/local/bin/vault egress        # prints "confined: effective group 'vault', ..."
   ```
   After every rebuild, repeat the `install` line. A build run straight from `.build/` is **not** confined; `vault sync` prints a warning then, and refuses with `VAULT_REQUIRE_EGRESS=1`.
3. Install the anchor and the hosts file:

   ```sh
   sudo cp scripts/egress/vault.pf.conf /etc/pf.anchors/vault
   sudo cp scripts/egress/vault.hosts /etc/pf.anchors/vault.hosts
   sudo sh -c 'grep -q "anchor \"vault\"" /etc/pf.conf || printf "\nanchor \"vault\"\nload anchor \"vault\" from \"/etc/pf.anchors/vault\"\n" >> /etc/pf.conf'
   sudo pfctl -f /etc/pf.conf
   sudo pfctl -e
   sudo pfctl -a vault -sr            # shows the two rules
   sudo pfctl -a vault -t vault_allow -T show   # shows the resolved provider addresses
   ```
4. Check from the confined binary: `/usr/local/bin/vault egress` says confined. Then smoke-test the rules themselves with a throwaway setgid copy of `curl`. macOS has no `sg`, and a setgid binary is how the sync gets the group anyway:

   ```sh
   sudo install -m 2755 -o root -g vault /usr/bin/curl /usr/local/bin/vault-curl-probe
   curl -sI --max-time 5 https://example.com | head -1                          # unconfined: answers
   /usr/local/bin/vault-curl-probe -sI --max-time 5 https://example.com         # confined: fails, not listed
   /usr/local/bin/vault-curl-probe -sI --max-time 5 https://fints.dkb.de | head -1   # confined: answers, listed
   sudo rm /usr/local/bin/vault-curl-probe
   ```
   The confined `example.com` call only shows the rule works if the unconfined one answered and the listed host answers through the same probe. A failure on all three is a network problem, not the allowlist.
5. Register the schedule from the installed binary, so the launch agent runs the confined one, and make it refuse to sync unconfined: `/usr/local/bin/vault schedule --require-egress`. That writes `VAULT_REQUIRE_EGRESS=1` into the launch agent's environment. Running `vault schedule` again later keeps the variable.

## Keeping the table fresh
`pf` resolves the hostnames when the table loads. Providers behind CDNs change addresses, so refresh the table regularly. The table lives inside the anchor, so the anchor must be named:

```sh
sudo pfctl -a vault -t vault_allow -T replace -f /etc/pf.anchors/vault.hosts
```

A sudoers entry limited to exactly that command lets the launch agent run it before each sync without a password prompt.

## Limits, so nobody over-trusts this
- **Verified by reading, not yet on a machine.** `group` matching in `pf`, the setgid bit on an APFS data volume and the anchor-scoped table refresh are all documented behaviour, but this repo's author has not run step 4 on the target Mac. Do it once and record the result here.
- DNS is not covered: lookups go through the system resolver daemon, which the rules don't match. A compromised process could leak a little through DNS names.
- The allowlist only helps if the sync really runs from the setgid binary. `vault egress` and the warning in `vault sync` exist to catch the case where it doesn't.
- Little Snitch, if installed, adds hostname-based rules per process and alerts on blocked attempts. Use both.
