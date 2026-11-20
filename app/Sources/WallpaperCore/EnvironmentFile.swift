import Foundation

/// A deliberately small .env reader. It never sources a shell or expands variables.
public enum EnvironmentFile {
    public static func parse(_ data: Data) throws -> [String: String] {
        guard data.count <= 65_536, let text = String(data: data, encoding: .utf8) else {
            throw NSError(domain: "EnvironmentFile", code: 1, userInfo: [NSLocalizedDescriptionKey: "配置文件必须是小于 64 KiB 的 UTF-8 文本"])
        }
        var result: [String: String] = [:]
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            guard let equal = line.firstIndex(of: "=") else { throw invalid() }
            let key = String(line[..<equal]).trimmingCharacters(in: .whitespaces)
            guard key.range(of: "^[A-Za-z_][A-Za-z0-9_]*$", options: .regularExpression) != nil,
                  result[key] == nil else { throw invalid() }
            var value = String(line[line.index(after: equal)...]).trimmingCharacters(in: .whitespaces)
            if let first = value.first, first == "\"" || first == "'" {
                guard value.count >= 2, value.last == first else { throw invalid() }
                value = String(value.dropFirst().dropLast())
            }
            result[key] = value
        }
        return result
    }
    private static func invalid() -> Error {
        NSError(domain: "EnvironmentFile", code: 2, userInfo: [NSLocalizedDescriptionKey: "使用 KEY=value 格式，每个名称只出现一次；不支持 export、插值或多行值"])
    }
}
