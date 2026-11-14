import Foundation
import VaultCore

/// Tells whether this process is the one the egress allowlist confines.
///
/// `scripts/egress/vault.pf.conf` matches outbound sockets by their effective group, `vault`.
/// The installed binary is setgid `vault`, so a sync started from it carries that group;
/// a build from `.build/` run by hand does not, and then the allowlist does not apply.
public enum EgressConfinement {
    public static let groupName = "vault"

    public enum Status: Equatable, Sendable {
        case confined(group: String)
        case unconfined(reason: String)
    }

    public static func status() -> Status {
        let gid = getegid()
        guard let entry = getgrgid(gid), let cName = entry.pointee.gr_name else {
            return .unconfined(reason: "effective group id \(gid) has no name")
        }
        let name = String(cString: cName)
        if name == groupName {
            return .confined(group: name)
        }
        return .unconfined(
            reason: "running with effective group '\(name)', not '\(groupName)'; "
                + "the pf allowlist in scripts/egress matches sockets by that group, so it does not apply to this process"
        )
    }
}
