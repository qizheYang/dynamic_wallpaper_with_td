import Foundation
import Darwin

@MainActor
final class DiagnosticLog {
    private let file: FileHandle
    let start = ProcessInfo.processInfo.systemUptime
    init(url: URL) throws {
        let descriptor = open(url.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard descriptor >= 0 else { throw StudioFailure.message("无法创建新的诊断日志") }
        file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
    }
    func write(_ object: [String: Any]) {
        var record = object
        record["elapsed_seconds"] = ProcessInfo.processInfo.systemUptime - start
        record["pid"] = ProcessInfo.processInfo.processIdentifier
        record["schema_version"] = 1
        if JSONSerialization.isValidJSONObject(record), let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) {
            try? file.write(contentsOf: data + Data([10]))
        }
    }
    func metrics() -> [String: Any] {
        var usage = rusage(); getrusage(RUSAGE_SELF, &usage)
        var vm = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &vm) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        var values: [String: Any] = ["cpu_seconds": Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec)
            + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1_000_000]
        if result == KERN_SUCCESS {
            values["physical_footprint_bytes"] = vm.phys_footprint
            values["resident_bytes"] = vm.resident_size
        }
        return values
    }
}
