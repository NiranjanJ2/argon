import Foundation
import Testing

@testable import foqos

struct ArgonMarkdownTests {
  @Test func aChecklistIsCheckboxesNotBullets() {
    // "- [ ] x" matches the bullet pattern too; the checkbox is the more
    // specific reading and has to win.
    let blocks = ArgonMarkdown.blocks("- [ ] HW 9\n- [x] AP Chem\n- Physics", messageID: "m")

    #expect(blocks.count == 3)
    #expect(blocks[0] == .checkbox(id: "m:0", text: "HW 9", checked: false))
    #expect(blocks[1] == .checkbox(id: "m:1", text: "AP Chem", checked: true))
    #expect(blocks[2] == .bullet("Physics"))
  }

  @Test func numberedListsKeepTheirNumbers() {
    let blocks = ArgonMarkdown.blocks("1. first\n2) second", messageID: "m")

    #expect(blocks == [.numbered(index: 1, text: "first"), .numbered(index: 2, text: "second")])
  }

  @Test func headingsAndRulesAreStructureNotText() {
    let blocks = ArgonMarkdown.blocks("## Tonight\n---\nplain line", messageID: "m")

    #expect(blocks[0] == .heading("Tonight"))
    #expect(blocks[1] == .divider)
    #expect(blocks[2] == .paragraph("plain line"))
  }

  @Test func blankLinesAreDroppedNotRendered() {
    let blocks = ArgonMarkdown.blocks("one\n\n\ntwo", messageID: "m")

    #expect(blocks == [.paragraph("one"), .paragraph("two")])
  }

  @Test func aStrayAsteriskIsNotSwallowed() {
    // Falling back to the raw string matters more than styling: dropping part
    // of what Argon said is the worse failure.
    let rendered = String(ArgonMarkdown.inline("2 * 3 is 6 and **this** is bold").characters)

    #expect(rendered.contains("2 * 3 is 6"))
    #expect(rendered.contains("this"))
    #expect(!rendered.contains("**"))
  }

  @Test func checkboxIdsAreStablePerMessageLine() {
    // The tick is stored against this id. If it moved between renders, a
    // checked box would come back empty on the next poll.
    let first = ArgonMarkdown.blocks("- [ ] a\n- [ ] b", messageID: "abc")
    let again = ArgonMarkdown.blocks("- [ ] a\n- [ ] b", messageID: "abc")

    #expect(first == again)
    #expect(first[1].id == "c:abc:1")
  }
}
