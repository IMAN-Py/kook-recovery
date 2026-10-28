import AppKit

final class DocumentStack: NSStackView {
    override var isFlipped: Bool { true }
}

final class WindowSurface: NSView {
    override func draw(_ dirtyRect: NSRect) {
        NSColor.windowBackgroundColor.setFill()
        dirtyRect.fill()
    }
}

final class DropZone: NSView {
    var onDrop: (([String]) -> Void)?
    var accepting = true
    private var highlighted = false
    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func draw(_ dirtyRect: NSRect) {
        let shape = NSBezierPath(roundedRect: bounds.insetBy(dx: 1, dy: 1), xRadius: 12, yRadius: 12)
        (highlighted ? NSColor.controlAccentColor.withAlphaComponent(0.10) : NSColor.controlBackgroundColor).setFill()
        shape.fill()
        (highlighted ? NSColor.controlAccentColor : NSColor.separatorColor).setStroke()
        shape.lineWidth = highlighted ? 2 : 1
        shape.stroke()
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard accepting, sender.draggingPasteboard.canReadObject(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) else { return [] }
        highlighted = true; needsDisplay = true
        return .copy
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { highlighted = false; needsDisplay = true }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        highlighted = false; needsDisplay = true
        guard accepting, let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL], !urls.isEmpty else { return false }
        onDrop?(urls.map(\.path))
        return true
    }
}

final class RecoveryApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var inputs: [String] = []
    var donor: String?
    var resultFolder: String?
    var worker: Process?
    var configURL: URL?
    var cancelled = false
    var finishedEvent = false
    var recoveredCount = 0
    var skippedCount = 0
    var smoke = false
    var smokeOutput = ""
    let dropZone = DropZone()
    let selectionRows = DocumentStack()
    let selectionScroll = NSScrollView()
    let resultPicker = NSPopUpButton()
    let openButton = NSButton(title: "Open file", target: nil, action: nil)
    let emptySymbol = NSImageView()
    let emptyTitle = NSTextField(labelWithString: "Your recovered files will appear here")
    let emptyHint = NSTextField(wrappingLabelWithString: "Add encrypted files, then choose Recover in place.")
    let previewContainer = NSBox()
    let elapsedLabel = NSTextField(labelWithString: "")
    let phaseLabel = NSTextField(labelWithString: "Add files to get started")
    let logScroll = NSScrollView()
    let detailsButton = NSButton(title: "Show activity", target: nil, action: nil)
    let optionsButton = NSButton(title: "Recovery options", target: nil, action: nil)
    var optionsView: NSView!
    var previewFiles: [String] = []
    var finishedFiles = 0
    var totalFiles = 0
    var startedAt: Date?
    var timer: Timer?
    var interactionChecksPassed = true
    let inputLabel = NSTextField(wrappingLabelWithString: "Choose encrypted photos or a folder to begin.")
    let donorLabel = NSTextField(wrappingLabelWithString: "Optional · improves camera matching")
    let status = NSTextField(labelWithString: "Ready")
    let previewLabel = NSTextField(wrappingLabelWithString: "A recovered photo will appear here.")
    let preview = NSImageView()
    let progress = NSProgressIndicator()
    let log = NSTextView()
    let rotate = NSPopUpButton()
    let recoverButton = NSButton(title: "Recover in place", target: nil, action: nil)
    let cancelButton = NSButton(title: "Stop", target: nil, action: nil)
    let revealButton = NSButton(title: "Show results", target: nil, action: nil)
    var editingButtons: [NSButton] = []

    func card(_ child: NSView) -> NSBox {
        let box = NSBox()
        box.boxType = .custom
        box.borderWidth = 1
        box.borderColor = .separatorColor
        box.fillColor = .controlBackgroundColor
        box.cornerRadius = 12
        box.contentViewMargins = .zero
        let host = box.contentView!
        child.translatesAutoresizingMaskIntoConstraints = false
        host.addSubview(child)
        NSLayoutConstraint.activate([
            child.leadingAnchor.constraint(equalTo: host.leadingAnchor, constant: 16),
            child.trailingAnchor.constraint(equalTo: host.trailingAnchor, constant: -16),
            child.topAnchor.constraint(equalTo: host.topAnchor, constant: 16),
            child.bottomAnchor.constraint(equalTo: host.bottomAnchor, constant: -16)
        ])
        return box
    }

    func flexibleSpace() -> NSView {
        let space = NSView()
        space.setContentHuggingPriority(.defaultLow, for: .horizontal)
        return space
    }

    func label(_ text: String, size: CGFloat = 13, bold: Bool = false) -> NSTextField {
        let v = NSTextField(wrappingLabelWithString: text)
        v.font = bold ? .boldSystemFont(ofSize: size) : .systemFont(ofSize: size)
        return v
    }

    func button(_ title: String, _ action: Selector) -> NSButton {
        let b = NSButton(title: title, target: self, action: action)
        b.bezelStyle = .rounded
        editingButtons.append(b)
        return b
    }

    func stack(_ views: [NSView], vertical: Bool = true, spacing: CGFloat = 12) -> NSStackView {
        let s = NSStackView(views: views)
        s.orientation = vertical ? .vertical : .horizontal
        s.alignment = vertical ? .leading : .centerY
        s.spacing = spacing
        return s
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        menu.addItem(item)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Djvu Recover", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Djvu Recover", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        menu.addItem(editItem)
        NSApp.mainMenu = menu

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1120, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Djvu Recover"
        window.minSize = NSSize(width: 1000, height: 740)
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName("RecoveryWindow")
        window.contentView = WindowSurface(frame: window.contentView!.bounds)
        let content = window.contentView!
        content.wantsLayer = true
        let logo = NSImageView(image: NSImage(systemSymbolName: "photo.badge.arrow.down", accessibilityDescription: "Djvu Recover")!)
        logo.contentTintColor = .controlAccentColor
        logo.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 32, weight: .medium)
        logo.widthAnchor.constraint(equalToConstant: 46).isActive = true
        let subtitle = label("Recover what survives. Keep what matters.", size: 13)
        subtitle.textColor = .secondaryLabelColor
        let titles = stack([label("Djvu Recover", size: 25, bold: true), subtitle], spacing: 4)
        let local = label("On-device recovery  ·  No backups", size: 12)
        local.textColor = .secondaryLabelColor
        let header = stack([logo, titles, flexibleSpace(), local], vertical: false, spacing: 12)

        inputLabel.stringValue = "No files selected"
        inputLabel.font = .systemFont(ofSize: 12, weight: .medium)
        donorLabel.textColor = .secondaryLabelColor
        donorLabel.maximumNumberOfLines = 2
        rotate.addItems(withTitles: ["No rotation", "90° clockwise", "180°", "270° clockwise"])
        rotate.selectItem(at: min(3, max(0, UserDefaults.standard.integer(forKey: "rotationOption"))))
        let dropIcon = NSImageView(image: NSImage(systemSymbolName: "square.and.arrow.down", accessibilityDescription: nil)!)
        dropIcon.contentTintColor = .controlAccentColor
        dropIcon.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 25, weight: .regular)
        let dropHint = label("Drop encrypted files or folders here", size: 12)
        dropHint.textColor = .secondaryLabelColor
        let dropContent = stack([dropIcon, label("Add files to recover", size: 15, bold: true), dropHint,
            stack([button("Add files…", #selector(chooseFiles)), button("Add folder…", #selector(chooseFolder))], vertical: false, spacing: 8)], spacing: 9)
        dropContent.alignment = .centerX
        dropContent.translatesAutoresizingMaskIntoConstraints = false
        dropZone.addSubview(dropContent)
        NSLayoutConstraint.activate([
            dropContent.leadingAnchor.constraint(equalTo: dropZone.leadingAnchor, constant: 16),
            dropContent.trailingAnchor.constraint(equalTo: dropZone.trailingAnchor, constant: -16),
            dropContent.topAnchor.constraint(equalTo: dropZone.topAnchor, constant: 18),
            dropContent.bottomAnchor.constraint(equalTo: dropZone.bottomAnchor, constant: -18)
        ])
        dropZone.onDrop = { [weak self] paths in self?.addInputs(paths) }
        dropZone.setAccessibilityLabel("Drop encrypted files or folders here")
        let clear = button("Clear all", #selector(clearInputs))
        clear.controlSize = .small
        let selectionHeader = stack([inputLabel, flexibleSpace(), clear], vertical: false, spacing: 6)
        selectionRows.orientation = .vertical
        selectionRows.alignment = .leading
        selectionRows.spacing = 6
        selectionRows.translatesAutoresizingMaskIntoConstraints = false
        selectionScroll.documentView = selectionRows
        selectionScroll.hasVerticalScroller = true
        selectionScroll.drawsBackground = false
        selectionScroll.heightAnchor.constraint(equalToConstant: 94).isActive = true
        selectionRows.widthAnchor.constraint(equalTo: selectionScroll.contentView.widthAnchor).isActive = true
        selectionRows.topAnchor.constraint(equalTo: selectionScroll.contentView.topAnchor).isActive = true
        let selection = stack([selectionHeader, selectionScroll], spacing: 8)
        selectionHeader.widthAnchor.constraint(equalTo: selection.widthAnchor).isActive = true
        selectionScroll.widthAnchor.constraint(equalTo: selection.widthAnchor).isActive = true

        let locationHint = label("Validated cropped results replace encrypted files in the same folders. Files that fail stay untouched. No backups are kept.", size: 12)
        locationHint.textColor = .secondaryLabelColor
        let outputContent = stack([label("Recover in the original folders", bold: true), locationHint], spacing: 8)
        for v in outputContent.arrangedSubviews { v.widthAnchor.constraint(equalTo: outputContent.widthAnchor).isActive = true }
        let outputCard = card(outputContent)
        optionsButton.target = self
        optionsButton.action = #selector(toggleOptions)
        optionsButton.bezelStyle = .disclosure
        optionsButton.setButtonType(.pushOnPushOff)
        optionsButton.toolTip = "Rotation and healthy reference files"
        let optionsHeading = stack([optionsButton, label("Recovery options", bold: true), flexibleSpace()], vertical: false, spacing: 8)
        optionsButton.title = ""
        let donorHelp = label("A healthy photo or video from the same camera can improve the result.", size: 11)
        donorHelp.textColor = .secondaryLabelColor
        let settings = stack([label("Rotate recovered photos", size: 12, bold: true), rotate,
            label("Reference files (optional)", size: 12, bold: true), donorHelp, donorLabel,
            stack([button("Choose folder…", #selector(chooseDonor)), button("Clear", #selector(clearDonor))], vertical: false, spacing: 8)], spacing: 8)
        for v in settings.arrangedSubviews { v.widthAnchor.constraint(equalTo: settings.widthAnchor).isActive = true }
        optionsView = settings
        optionsView.isHidden = true
        let options = stack([optionsHeading, settings], spacing: 14)
        options.detachesHiddenViews = true
        optionsHeading.widthAnchor.constraint(equalTo: options.widthAnchor).isActive = true
        settings.widthAnchor.constraint(equalTo: options.widthAnchor).isActive = true
        let sidebar = DocumentStack(views: [dropZone, selection, outputCard, card(options)])
        sidebar.orientation = .vertical
        sidebar.alignment = .leading
        sidebar.spacing = 16
        sidebar.translatesAutoresizingMaskIntoConstraints = false
        for v in sidebar.arrangedSubviews { v.widthAnchor.constraint(equalTo: sidebar.widthAnchor).isActive = true }
        let sidebarScroll = NSScrollView()
        sidebarScroll.drawsBackground = false
        sidebarScroll.hasVerticalScroller = true
        sidebarScroll.documentView = sidebar
        sidebarScroll.widthAnchor.constraint(equalToConstant: 330).isActive = true
        sidebar.widthAnchor.constraint(equalTo: sidebarScroll.contentView.widthAnchor).isActive = true
        sidebar.topAnchor.constraint(equalTo: sidebarScroll.contentView.topAnchor).isActive = true

        preview.imageScaling = .scaleProportionallyUpOrDown
        preview.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        preview.setContentCompressionResistancePriority(.defaultLow, for: .vertical)
        previewContainer.boxType = .custom
        previewContainer.borderWidth = 1
        previewContainer.borderColor = .separatorColor
        previewContainer.fillColor = .controlBackgroundColor
        previewContainer.cornerRadius = 14
        previewContainer.contentViewMargins = .zero
        let previewHost = previewContainer.contentView!
        preview.translatesAutoresizingMaskIntoConstraints = false
        previewHost.addSubview(preview)
        NSLayoutConstraint.activate([
            preview.leadingAnchor.constraint(equalTo: previewHost.leadingAnchor, constant: 14),
            preview.trailingAnchor.constraint(equalTo: previewHost.trailingAnchor, constant: -14),
            preview.topAnchor.constraint(equalTo: previewHost.topAnchor, constant: 14),
            preview.bottomAnchor.constraint(equalTo: previewHost.bottomAnchor, constant: -14)
        ])
        emptySymbol.image = NSImage(systemSymbolName: "photo.on.rectangle.angled", accessibilityDescription: nil)
        emptySymbol.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 44, weight: .ultraLight)
        emptySymbol.contentTintColor = .tertiaryLabelColor
        emptyTitle.font = .systemFont(ofSize: 16, weight: .medium)
        emptyHint.textColor = .secondaryLabelColor
        emptyHint.alignment = .center
        emptyHint.preferredMaxLayoutWidth = 330
        let empty = stack([emptySymbol, emptyTitle, emptyHint], spacing: 14)
        empty.alignment = .centerX
        empty.translatesAutoresizingMaskIntoConstraints = false
        previewHost.addSubview(empty)
        NSLayoutConstraint.activate([empty.centerXAnchor.constraint(equalTo: previewHost.centerXAnchor), empty.centerYAnchor.constraint(equalTo: previewHost.centerYAnchor), empty.widthAnchor.constraint(lessThanOrEqualTo: previewHost.widthAnchor, constant: -32)])
        resultPicker.target = self
        resultPicker.action = #selector(selectResult)
        resultPicker.addItem(withTitle: "No results yet")
        resultPicker.isEnabled = false
        resultPicker.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        resultPicker.setAccessibilityLabel("Recovered file to preview")
        openButton.target = self
        openButton.action = #selector(openResult)
        openButton.bezelStyle = .rounded
        openButton.isEnabled = false
        let resultsHeader = stack([label("Recovered files", size: 15, bold: true), flexibleSpace(), openButton], vertical: false)
        previewLabel.stringValue = "Recovery is partial. Missing pixels and original colours may not be recoverable."
        previewLabel.font = .systemFont(ofSize: 11)
        previewLabel.textColor = .secondaryLabelColor
        let right = stack([resultsHeader, resultPicker, previewContainer, previewLabel], spacing: 12)
        for v in right.arrangedSubviews { v.widthAnchor.constraint(equalTo: right.widthAnchor).isActive = true }
        previewContainer.heightAnchor.constraint(greaterThanOrEqualToConstant: 290).isActive = true
        let body = stack([sidebarScroll, right], vertical: false, spacing: 24)
        body.alignment = .top
        right.widthAnchor.constraint(equalTo: body.widthAnchor, constant: -354).isActive = true
        sidebarScroll.heightAnchor.constraint(equalTo: body.heightAnchor).isActive = true
        right.heightAnchor.constraint(equalTo: body.heightAnchor).isActive = true

        logScroll.hasVerticalScroller = true
        logScroll.borderType = .bezelBorder
        log.isEditable = false
        log.isSelectable = true
        log.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        log.textContainerInset = NSSize(width: 10, height: 8)
        log.autoresizingMask = [.width]
        log.textContainer?.widthTracksTextView = true
        logScroll.documentView = log
        logScroll.heightAnchor.constraint(equalToConstant: 110).isActive = true
        logScroll.isHidden = true
        progress.style = .bar
        progress.isIndeterminate = true
        progress.isHidden = true
        status.font = .systemFont(ofSize: 13, weight: .semibold)
        status.lineBreakMode = .byTruncatingMiddle
        phaseLabel.font = .systemFont(ofSize: 11)
        phaseLabel.textColor = .secondaryLabelColor
        phaseLabel.lineBreakMode = .byTruncatingMiddle
        elapsedLabel.font = .monospacedDigitSystemFont(ofSize: 11, weight: .regular)
        elapsedLabel.textColor = .secondaryLabelColor
        recoverButton.target = self
        recoverButton.action = #selector(startRecovery)
        recoverButton.bezelStyle = .rounded
        recoverButton.controlSize = .large
        recoverButton.keyEquivalent = "\r"
        recoverButton.isEnabled = false
        cancelButton.target = self
        cancelButton.action = #selector(stopRecovery)
        cancelButton.bezelStyle = .rounded
        cancelButton.isHidden = true
        revealButton.target = self
        revealButton.action = #selector(showResults)
        revealButton.bezelStyle = .rounded
        revealButton.isHidden = true
        detailsButton.target = self
        detailsButton.action = #selector(toggleDetails)
        detailsButton.bezelStyle = .rounded
        detailsButton.controlSize = .small
        let statusStack = stack([status, phaseLabel], spacing: 4)
        statusStack.widthAnchor.constraint(lessThanOrEqualToConstant: 400).isActive = true
        let footer = stack([statusStack, elapsedLabel, flexibleSpace(), detailsButton, revealButton, cancelButton, recoverButton], vertical: false, spacing: 10)
        let root = stack([header, body, logScroll, progress, footer], spacing: 20)
        root.detachesHiddenViews = true
        root.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(root)
        NSLayoutConstraint.activate([
            root.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            root.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            root.topAnchor.constraint(equalTo: content.topAnchor, constant: 22),
            root.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -20)
        ])
        for v in root.arrangedSubviews { v.widthAnchor.constraint(equalTo: root.widthAnchor).isActive = true }
        refreshInputs()
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        let args = CommandLine.arguments
        if let index = args.firstIndex(of: "--smoke-test"), args.count > index + 2 {
            smoke = true
            inputs = [args[index + 1]]
            smokeOutput = args[index + 2] + "-diagnostics"
            if args.contains("--expect-skipped") { smokeOutput = args[index + 2] + "-rerun-diagnostics" }
            if args.contains("--rotate-180") { rotate.selectItem(at: 2) }
            // Exercise the same selection/drop and disclosure handlers used interactively.
            let sample = args[index + 1]
            let remove = NSButton()
            remove.identifier = NSUserInterfaceItemIdentifier(sample)
            removeInput(remove)
            interactionChecksPassed = inputs.isEmpty
            dropZone.onDrop?([sample, sample])
            interactionChecksPassed = interactionChecksPassed && inputs.count == 1
            // Missing inputs are allowed only for the explicit failure test.
            if inputs.isEmpty { inputs = [sample] }
            optionsButton.state = .on
            toggleOptions()
            interactionChecksPassed = interactionChecksPassed && !optionsView.isHidden
            optionsButton.state = .off
            toggleOptions()
            toggleDetails()
            interactionChecksPassed = interactionChecksPassed && !logScroll.isHidden
            toggleDetails()
            refreshInputs()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                self.startRecovery()
                if args.contains("--cancel-test") {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { if self.worker != nil { self.stopRecovery() } }
                }
            }
        }
    }

    func panel(files: Bool, folders: Bool, multiple: Bool = false) -> NSOpenPanel {
        let p = NSOpenPanel()
        p.canChooseFiles = files
        p.canChooseDirectories = folders
        p.allowsMultipleSelection = multiple
        return p
    }

    @objc func chooseFiles() {
        guard worker == nil else { return }
        let p = panel(files: true, folders: false, multiple: true)
        p.beginSheetModal(for: window) { result in
            if result == .OK { self.addInputs(p.urls.map(\.path)) }
        }
    }
    @objc func chooseFolder() {
        guard worker == nil else { return }
        let p = panel(files: false, folders: true, multiple: true)
        p.beginSheetModal(for: window) { result in
            if result == .OK { self.addInputs(p.urls.map(\.path)) }
        }
    }
    @objc func chooseDonor() {
        let p = panel(files: false, folders: true)
        p.beginSheetModal(for: window) { result in
            if result == .OK, let url = p.url { self.donor = url.path; self.donorLabel.stringValue = url.path }
        }
    }
    @objc func clearDonor() { donor = nil; donorLabel.stringValue = "Optional · improves camera matching" }
    func addInputs(_ paths: [String]) {
        guard worker == nil else { return }
        var known = Set(inputs)
        for path in paths {
            let canonical = URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath().path
            if FileManager.default.fileExists(atPath: canonical), known.insert(canonical).inserted {
                inputs.append(canonical)
            }
        }
        refreshInputs()
    }
    @objc func clearInputs() {
        guard worker == nil else { return }
        inputs.removeAll()
        refreshInputs()
    }
    @objc func removeInput(_ sender: NSButton) {
        guard worker == nil, let path = sender.identifier?.rawValue else { return }
        inputs.removeAll { $0 == path }
        refreshInputs()
    }
    func refreshInputs() {
        inputLabel.stringValue = inputs.isEmpty ? "No files selected" : "\(inputs.count) selected"
        inputLabel.toolTip = inputs.joined(separator: "\n")
        for row in selectionRows.arrangedSubviews { selectionRows.removeArrangedSubview(row); row.removeFromSuperview() }
        if inputs.isEmpty {
            let hint = label("JPEG photos and MP4 / MOV videos", size: 11)
            hint.textColor = .tertiaryLabelColor
            selectionRows.addArrangedSubview(hint)
        }
        for path in inputs {
            var directory: ObjCBool = false
            FileManager.default.fileExists(atPath: path, isDirectory: &directory)
            let icon = NSImageView(image: NSImage(systemSymbolName: directory.boolValue ? "folder" : "doc", accessibilityDescription: nil)!)
            icon.contentTintColor = .secondaryLabelColor
            icon.widthAnchor.constraint(equalToConstant: 18).isActive = true
            let name = NSTextField(labelWithString: URL(fileURLWithPath: path).lastPathComponent)
            name.font = .systemFont(ofSize: 12)
            name.lineBreakMode = .byTruncatingMiddle
            name.toolTip = path
            name.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            let remove = NSButton(image: NSImage(systemSymbolName: "xmark.circle.fill", accessibilityDescription: "Remove \(name.stringValue)")!, target: self, action: #selector(removeInput(_:)))
            remove.identifier = NSUserInterfaceItemIdentifier(path)
            remove.isBordered = false
            remove.contentTintColor = .tertiaryLabelColor
            remove.isEnabled = worker == nil
            let row = stack([icon, name, flexibleSpace(), remove], vertical: false, spacing: 7)
            selectionRows.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: selectionRows.widthAnchor, constant: -6).isActive = true
        }
        recoverButton.isEnabled = !inputs.isEmpty && worker == nil
        if worker == nil && resultFolder == nil && startedAt == nil {
            status.stringValue = inputs.isEmpty ? "Ready" : "Ready to recover"
            phaseLabel.stringValue = inputs.isEmpty ? "Add files to get started" : "Replace successful files here · leave failed files untouched"
        }
    }
    @objc func toggleOptions() { optionsView.isHidden = optionsButton.state != .on }
    @objc func toggleDetails() {
        logScroll.isHidden.toggle()
        detailsButton.title = logScroll.isHidden ? "Show activity" : "Hide activity"
    }
    func showError(_ message: String) {
        status.stringValue = "Could not complete recovery"
        phaseLabel.stringValue = message
        phaseLabel.toolTip = message
        append(message)
        if logScroll.isHidden { toggleDetails() }
    }
    func showEmpty(_ title: String, _ hint: String) {
        emptyTitle.stringValue = title
        emptyHint.stringValue = hint
        emptySymbol.isHidden = false; emptyTitle.isHidden = false; emptyHint.isHidden = false
    }
    @objc func selectResult() {
        let index = resultPicker.indexOfSelectedItem
        guard previewFiles.indices.contains(index) else { return }
        let path = previewFiles[index]
        let url = URL(fileURLWithPath: path)
        if ["jpg", "jpeg", "jfif"].contains(url.pathExtension.lowercased()), let image = NSImage(contentsOfFile: path) {
            preview.image = image
            emptySymbol.isHidden = true; emptyTitle.isHidden = true; emptyHint.isHidden = true
            previewLabel.stringValue = "\(Int(image.size.width)) × \(Int(image.size.height)) · Partial reconstruction. Check colours and orientation."
        } else {
            preview.image = nil
            showEmpty("Recovered video", "Choose Open file to play this result in your default video player.")
            previewLabel.stringValue = "The beginning of the video may be damaged."
        }
        previewLabel.toolTip = path
        openButton.isEnabled = true
    }
    @objc func openResult() {
        let index = resultPicker.indexOfSelectedItem
        if previewFiles.indices.contains(index) { NSWorkspace.shared.open(URL(fileURLWithPath: previewFiles[index])) }
    }
    func updateElapsed() {
        guard let start = startedAt else { return }
        let seconds = max(0, Int(Date().timeIntervalSince(start)))
        elapsedLabel.stringValue = String(format: "%d:%02d elapsed", seconds / 60, seconds % 60)
    }
    func append(_ text: String) {
        log.textStorage?.append(NSAttributedString(string: text + "\n", attributes: [.font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular), .foregroundColor: NSColor.labelColor]))
        log.scrollToEndOfDocument(nil)
    }
    func busy(_ value: Bool) {
        editingButtons.forEach { $0.isEnabled = !value }
        rotate.isEnabled = !value
        recoverButton.isEnabled = !value && !inputs.isEmpty
        cancelButton.isEnabled = value
        cancelButton.isHidden = !value
        dropZone.accepting = !value
        optionsButton.isEnabled = !value
        refreshInputs()
        progress.isHidden = !value
        if value { progress.startAnimation(nil) } else { progress.stopAnimation(nil) }
    }

    @objc func startRecovery() {
        guard worker == nil, !inputs.isEmpty else { return }
        cancelled = false
        finishedEvent = false
        recoveredCount = 0
        skippedCount = 0
        finishedFiles = 0
        totalFiles = 0
        previewFiles.removeAll()
        resultPicker.removeAllItems()
        resultPicker.addItem(withTitle: "Waiting for results…")
        resultPicker.isEnabled = false
        openButton.isEnabled = false
        resultFolder = nil
        revealButton.isEnabled = false
        revealButton.isHidden = true
        preview.image = nil
        showEmpty("Recovering your files", "You can keep this window open. Large photos may take a few minutes.")
        previewLabel.stringValue = "Results will appear here as each file finishes."
        log.string = ""
        status.stringValue = "Starting…"
        phaseLabel.stringValue = "Preparing recovery"
        phaseLabel.toolTip = nil
        progress.isIndeterminate = true
        startedAt = Date()
        updateElapsed()
        if !smoke {
            UserDefaults.standard.set(rotate.indexOfSelectedItem, forKey: "rotationOption")
        }
        do {
            let resources = Bundle.main.resourceURL!
            let bundledPython = resources.appendingPathComponent("runtime/DjvuPython")
            guard FileManager.default.isExecutableFile(atPath: bundledPython.path) else {
                throw NSError(domain: "Recovery", code: 1, userInfo: [NSLocalizedDescriptionKey: "Python runtime is missing. Rebuild with build-macos.sh in the djvu-recover folder."])
            }
            let python = bundledPython.path
            let config: [String: Any] = ["inputs": inputs, "donors": donor.map { [$0] } ?? [],
                                         "grey": "crop", "rotate": rotate.indexOfSelectedItem * 90]
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("djvu-job-\(UUID().uuidString).json")
            try JSONSerialization.data(withJSONObject: config).write(to: url)
            configURL = url
            let process = Process()
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = ["-u", resources.appendingPathComponent("engine/app_backend.py").path, url.path]
            process.currentDirectoryURL = resources.appendingPathComponent("engine")
            var environment = ProcessInfo.processInfo.environment
            environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
            environment["PYTHONUNBUFFERED"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            let cache = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent("local.djvu-recover.desktop")
            try FileManager.default.createDirectory(at: cache, withIntermediateDirectories: true)
            let library = cache.appendingPathComponent("library.json")
            let seed = resources.appendingPathComponent("engine/.cache/library.json")
            if !FileManager.default.fileExists(atPath: library.path), FileManager.default.fileExists(atPath: seed.path) {
                try FileManager.default.copyItem(at: seed, to: library)
            }
            environment["DJVU_RECOVER_CACHE_DIR"] = cache.path
            process.environment = environment
            let pipe = Pipe()
            process.standardOutput = pipe
            // A separate pipe prevents engine warnings from corrupting JSON events.
            let errors = Pipe()
            process.standardError = errors
            errors.fileHandleForReading.readabilityHandler = { handle in
                let bytes = handle.availableData
                if bytes.isEmpty { handle.readabilityHandler = nil; return }
                if let text = String(data: bytes, encoding: .utf8) {
                    DispatchQueue.main.async { self.append(text.trimmingCharacters(in: .newlines)) }
                }
            }
            try process.run()
            worker = process
            busy(true)
            timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.updateElapsed() }
            DispatchQueue.global(qos: .userInitiated).async {
                var buffer = Data()
                while true {
                    let bytes = pipe.fileHandleForReading.availableData
                    if bytes.isEmpty { break }
                    buffer.append(bytes)
                    while let end = buffer.firstIndex(of: 10) {
                        let line = Data(buffer[..<end])
                        buffer.removeSubrange(...end)
                        if let event = try? JSONSerialization.jsonObject(with: line) as? [String: Any] {
                            DispatchQueue.main.async { self.handle(event) }
                        }
                    }
                }
                process.waitUntilExit()
                errors.fileHandleForReading.readabilityHandler = nil
                DispatchQueue.main.async { self.completed(process.terminationStatus) }
            }
        } catch {
            busy(false)
            showError(error.localizedDescription)
            showEmpty("Recovery could not start", "Open the activity details below for the reason.")
            if let url = configURL { try? FileManager.default.removeItem(at: url) }
            configURL = nil
            if smoke { finishSmoke(success: false) }
        }
    }

    func handle(_ event: [String: Any]) {
        switch event["event"] as? String {
        case "message":
            let message = event["text"] as? String ?? ""
            append(message)
            if totalFiles == 0 { status.stringValue = "Inspecting files…" }
            phaseLabel.stringValue = message
        case "started":
            resultFolder = event["output"] as? String
            totalFiles = event["total"] as? Int ?? 0
            progress.isIndeterminate = false
            progress.minValue = 0
            progress.maxValue = Double(max(1, totalFiles))
            progress.doubleValue = 0
            status.stringValue = "Preparing \(totalFiles) file(s)…"
            append("Found \(event["total"] ?? 0) encrypted file(s).")
        case "progress":
            status.stringValue = "Recovering \(event["index"] ?? 0) of \(event["total"] ?? 0)…"
            phaseLabel.stringValue = event["name"] as? String ?? ""
            phaseLabel.toolTip = phaseLabel.stringValue
            append("Rebuilding \(event["name"] ?? "")…")
        case "result":
            finishedFiles += 1
            progress.doubleValue = Double(finishedFiles)
            if event["status"] as? String == "partial" {
                recoveredCount += 1
                append("Partial recovery: \(event["detail"] ?? "")")
                if let files = event["files"] as? [String] {
                    if let source = event["source"] as? String, let index = inputs.firstIndex(of: source), let restored = files.first {
                        inputs[index] = restored
                    }
                    let previous = previewFiles.count
                    if previous == 0 { resultPicker.removeAllItems() }
                    previewFiles.append(contentsOf: files)
                    for file in files {
                        resultPicker.addItem(withTitle: "\(resultPicker.numberOfItems + 1) · \(URL(fileURLWithPath: file).lastPathComponent)")
                        resultPicker.lastItem?.toolTip = file
                    }
                    resultPicker.isEnabled = !previewFiles.isEmpty
                    resultPicker.selectItem(at: previous)
                    selectResult()
                }
            } else if event["status"] as? String == "skipped" {
                skippedCount += 1
                append(event["detail"] as? String ?? "Skipped: output already exists.")
            } else {
                append("Could not recover: \(event["source"] ?? "")\n\(event["error"] ?? "Unknown error")")
                if logScroll.isHidden { toggleDetails() }
            }
        case "finished":
            finishedEvent = true
            recoveredCount = event["recovered"] as? Int ?? 0
            skippedCount = event["skipped"] as? Int ?? 0
            resultFolder = event["output"] as? String
            status.stringValue = "\(recoveredCount) recovered · \(skippedCount) skipped · \(event["failed"] ?? 0) failed"
            phaseLabel.stringValue = "Successful originals replaced · failed files left unchanged"
            phaseLabel.toolTip = resultFolder
            append("Recovered in the original folder: \(resultFolder ?? "")")
        case "error":
            showError(event["text"] as? String ?? "Unknown error")
        default: break
        }
    }

    func completed(_ code: Int32) {
        worker = nil
        timer?.invalidate()
        timer = nil
        updateElapsed()
        busy(false)
        if cancelled {
            status.stringValue = "Recovery stopped"
            phaseLabel.stringValue = "\(recoveredCount) completed result(s) saved · you can try again"
        } else if !finishedEvent && status.stringValue != "Could not complete recovery" {
            showError("The recovery worker stopped unexpectedly. Try again or inspect the activity details.")
        }
        if recoveredCount == 0 {
            previewLabel.stringValue = skippedCount > 0 ? "Existing files are kept unchanged, without renaming or extra copies." : "Adding healthy reference files from the same camera may help."
            showEmpty(cancelled ? "Recovery stopped" : (skippedCount > 0 ? "Files already exist" : "No files recovered"), cancelled ? "Unfinished files are unchanged. Completed files have been replaced." : (skippedCount > 0 ? "Choose Show results to view the output folder." : "Check the activity details below for the reason."))
            if !cancelled && logScroll.isHidden { toggleDetails() }
            resultPicker.removeAllItems()
            resultPicker.addItem(withTitle: "No recovered files")
        }
        revealButton.isEnabled = resultFolder != nil
        revealButton.isHidden = resultFolder == nil
        if let url = configURL { try? FileManager.default.removeItem(at: url) }
        configURL = nil
        if smoke {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                let args = CommandLine.arguments
                let controlsRestored = self.recoverButton.isEnabled && self.dropZone.accepting && !self.cancelButton.isEnabled
                if args.contains("--expect-error") {
                    self.finishSmoke(success: code != 0 && !self.logScroll.isHidden && self.previewFiles.isEmpty && controlsRestored)
                } else if args.contains("--cancel-test") {
                    self.finishSmoke(success: self.cancelled && self.worker == nil && controlsRestored)
                } else if args.contains("--expect-skipped") {
                    self.finishSmoke(success: code == 0 && self.finishedEvent && self.skippedCount == self.totalFiles && self.recoveredCount == 0 && self.previewFiles.isEmpty && controlsRestored)
                } else if args.contains("--expect-mixed") {
                    self.finishSmoke(success: code == 2 && self.finishedEvent && self.recoveredCount > 0 && self.preview.image != nil && self.finishedFiles == self.totalFiles && controlsRestored)
                } else {
                    let firstImage = self.preview.image != nil
                    if self.previewFiles.count > 1 { self.resultPicker.selectItem(at: 1); self.selectResult() }
                    self.finishSmoke(success: code == 0 && self.finishedEvent && self.recoveredCount > 0 && firstImage && self.preview.image != nil && self.previewFiles.count == self.resultPicker.numberOfItems && controlsRestored && self.finishedFiles == self.totalFiles)
                }
            }
        }
    }
    @objc func stopRecovery() {
        cancelled = true
        cancelButton.isEnabled = false
        status.stringValue = "Stopping…"
        worker?.terminate()
    }
    @objc func showResults() {
        if let path = resultFolder { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
    }
    func finishSmoke(success: Bool) {
        let success = success && interactionChecksPassed
        let folder = URL(fileURLWithPath: smokeOutput)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        window.contentView?.layoutSubtreeIfNeeded()
        if let view = window.contentView {
            view.effectiveAppearance.performAsCurrentDrawingAppearance {
                view.displayIfNeeded()
                if let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                    view.cacheDisplay(in: view.bounds, to: bitmap)
                    try? bitmap.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent("app-preview.png"))
                }
            }
        }
        let data: [String: Any] = ["passed": success, "status": status.stringValue, "preview_loaded": preview.image != nil,
                                   "results": resultFolder ?? "", "log": log.string,
                                   "result_count": previewFiles.count, "finished_files": finishedFiles,
                                   "files": previewFiles,
                                   "skipped": skippedCount,
                                   "total_files": totalFiles, "activity_visible": !logScroll.isHidden,
                                   "recover_enabled": recoverButton.isEnabled, "drop_enabled": dropZone.accepting,
                                   "selection_and_disclosure_checks": interactionChecksPassed,
                                   "elapsed": elapsedLabel.stringValue]
        try? JSONSerialization.data(withJSONObject: data, options: [.prettyPrinted, .sortedKeys]).write(to: folder.appendingPathComponent("app-smoke-test.json"))
        print("APP_SMOKE_TEST \(success ? "PASS" : "FAIL")")
        NSApp.terminate(nil)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard worker != nil, !smoke else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "Stop recovery and quit?"
        alert.informativeText = "Completed results are saved. The current file has not finished."
        alert.addButton(withTitle: "Keep recovering")
        alert.addButton(withTitle: "Stop and quit")
        if alert.runModal() == .alertFirstButtonReturn {
            window.makeKeyAndOrderFront(nil)
            return .terminateCancel
        }
        return .terminateNow
    }
    func applicationWillTerminate(_ notification: Notification) { worker?.terminate() }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = RecoveryApp()
app.delegate = delegate
app.run()
