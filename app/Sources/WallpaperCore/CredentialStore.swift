import Foundation
import Security

public enum CredentialError: Error, LocalizedError {
    case invalidEndpoint, invalidCredential, missing, originMismatch, keychain(OSStatus)
    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint: return "Use an HTTPS endpoint without URL credentials, query parameters, or fragments."
        case .invalidCredential: return "Enter a nonempty API key and credential reference."
        case .missing: return "No API key is saved for this connection."
        case .originMismatch: return "This credential belongs to another endpoint origin. Save a key for this connection."
        case .keychain(let status): return "Keychain operation failed (status \(status))."
        }
    }
}

public func credentialOrigin(_ endpoint: URL) throws -> String {
    guard endpoint.scheme?.lowercased() == "https", let host = endpoint.host, !host.isEmpty,
          endpoint.user == nil, endpoint.password == nil, endpoint.query == nil,
          endpoint.fragment == nil else { throw CredentialError.invalidEndpoint }
    return "https://\(host.lowercased()):\(endpoint.port ?? 443)"
}

public protocol CredentialStore {
    func read(reference: String, endpoint: URL) throws -> String
    func save(secret: String, reference: String, endpoint: URL) throws
    func delete(reference: String) throws
}

/// Secrets stay in a non-synchronizing, device-local Keychain item. Profiles store references only.
public final class KeychainCredentialStore: CredentialStore {
    private struct Entry: Codable { let origin: String; let secret: String }
    private let service: String
    public init(service: String = "org.tdwallpaper.agent-credentials") { self.service = service }

    private func query(_ reference: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: reference, kSecAttrSynchronizable as String: false]
    }

    public func read(reference: String, endpoint: URL) throws -> String {
        let origin = try credentialOrigin(endpoint)
        var request = query(reference)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var value: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &value)
        if status == errSecItemNotFound { throw CredentialError.missing }
        guard status == errSecSuccess else { throw CredentialError.keychain(status) }
        guard let data = value as? Data, let entry = try? JSONDecoder().decode(Entry.self, from: data)
        else { throw CredentialError.invalidCredential }
        guard entry.origin == origin else { throw CredentialError.originMismatch }
        return entry.secret
    }

    public func save(secret: String, reference: String, endpoint: URL) throws {
        guard !reference.isEmpty, reference.utf8.count <= 128, !secret.isEmpty,
              secret.utf8.count <= 8192, !secret.contains("\n"), !secret.contains("\r")
        else { throw CredentialError.invalidCredential }
        let data = try JSONEncoder().encode(Entry(origin: credentialOrigin(endpoint), secret: secret))
        let attributes: [String: Any] = [kSecValueData as String: data,
                                         kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let status = SecItemUpdate(query(reference) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query(reference)
            attributes.forEach { item[$0.key] = $0.value }
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw CredentialError.keychain(added) }
        } else if status != errSecSuccess { throw CredentialError.keychain(status) }
    }

    public func delete(reference: String) throws {
        let status = SecItemDelete(query(reference) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw CredentialError.keychain(status) }
    }
}
