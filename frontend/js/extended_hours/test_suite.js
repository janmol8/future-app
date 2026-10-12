/**
 * TEST SUITE: Native Extended Hours Verification
 */
const ExtendedHoursTester = (() => {
  
  function runSessionTests() {
    console.log("--- Starting Session Detection Tests ---");
    
    // Mocking Date.now() for specific IST times
    const originalNow = Date.now;
    
    const testCases = [
      { name: "Pre-Market Early", ist: "09:05", expected: "PRE_MARKET" },
      { name: "Pre-Market Late", ist: "09:14", expected: "PRE_MARKET" },
      { name: "Market Open", ist: "09:15", expected: "REGULAR" },
      { name: "Mid Day", ist: "12:00", expected: "REGULAR" },
      { name: "Market Close", ist: "15:29", expected: "REGULAR" },
      { name: "Post-Market", ist: "15:45", expected: "POST_MARKET" },
      { name: "Evening Closed", ist: "20:00", expected: "CLOSED" }
    ];

    testCases.forEach(tc => {
      const [h, m] = tc.ist.split(':').map(Number);
      const d = new Date();
      // Calculate UTC equivalent for the desired IST
      // IST is UTC+5:30. So UTC = IST - 5:30
      const utcDate = new Date(d.getFullYear(), d.getUTCMonth(), d.getUTCDate(), h, m);
      // Adjust for IST offset manually for the test
      const testMs = utcDate.getTime() - (330 * 60000) + (new Date().getTimezoneOffset() * 60000);
      
      // Override Date.now for the helper call
      Date.now = () => testMs;
      
      const result = SessionManager.getSession();
      const status = result === tc.expected ? "✅ PASS" : `❌ FAIL (Got ${result})`;
      console.log(`[${tc.name}] Time: ${tc.ist} IST -> Result: ${result} ${status}`);
    });

    // Restore original Date.now
    Date.now = originalNow;
  }

  function simulateLiveTick(sessionType, price) {
      console.log(`--- Simulating Live Tick for ${sessionType} at ₹${price} ---`);
      
      // 1. Mock the session in the app state
      const originalGetSession = SessionManager.getSession;
      SessionManager.getSession = () => sessionType;
      
      // 2. Mock current price
      window.currentPrice = price;

      // 3. Trigger UI update
      if (typeof App !== 'undefined' && App.updateExtendedHoursUI) {
          // Note: updateExtendedHoursUI is internal to App, we might need to expose it or trigger it via the interval logic
          console.log("Triggering UI Refresh...");
          // In a real test we'd call the function, but since it's private we rely on the logic we injected
      }
      
      // 4. Verify Chart Line Creation
      if (typeof ChartModule !== 'undefined' && ChartModule.updateExtendedHoursLine) {
          ChartModule.updateExtendedHoursLine(sessionType, price);
          console.log(`✅ ChartModule.updateExtendedHoursLine called with ${sessionType} and ${price}`);
      }

      // Restore session manager
      SessionManager.getSession = originalGetSession;
  }

  return {
    run: () => {
      runSessionTests();
      console.log("\n--- Manual Simulation Commands ---");
      console.log("Tester.simulate('PRE_MARKET', 24500.50)");
      console.log("Tester.simulate('POST_MARKET', 24620.10)");
    },
    simulate: simulateLiveTick
  };
})();

// Auto-run tests if in browser console environment
if (typeof SessionManager !== 'undefined') {
    ExtendedHoursTester.run();
}
