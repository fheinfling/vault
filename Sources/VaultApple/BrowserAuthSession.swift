import Foundation
import VaultCore

/// OAuth from a command-line process: open the provider's page in the default browser and
/// wait for the redirect. The app shell uses `ASWebAuthenticationSession` instead.
///
/// Not implemented yet: the callback listener comes with the Scalable spike, where the
/// redirect URL requirements are known.
public struct BrowserAuthSession: AuthSession {
    public init() {}

    public func authorize(url: URL, callbackScheme: String) async throws -> URL {
        throw ConnectorError.notImplemented("browser OAuth session, comes with the Scalable spike")
    }
}
