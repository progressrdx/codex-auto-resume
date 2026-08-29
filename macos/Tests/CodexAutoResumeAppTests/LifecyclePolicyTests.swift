import XCTest
@testable import CodexAutoResumeApp

final class LifecyclePolicyTests: XCTestCase {
    func testOrdinaryQuitNeedsNoWarning() {
        XCTAssertFalse(LifecyclePolicy.shouldWarn(activeMonitorCount: 0, hasUncertainOperation: false))
    }

    func testActiveOrUncertainWorkWarnsBeforeQuit() {
        XCTAssertTrue(LifecyclePolicy.shouldWarn(activeMonitorCount: 1, hasUncertainOperation: false))
        XCTAssertTrue(LifecyclePolicy.shouldWarn(activeMonitorCount: 0, hasUncertainOperation: true))
    }
}
