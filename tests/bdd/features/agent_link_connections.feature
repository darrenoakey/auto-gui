Feature: Open agent-link from Auto GUI

  Scenario: Dashboard shows agent-link navigation
    Given Auto GUI is running
    When I browse to "/"
    Then the dashboard is visible
    And the dashboard shows "agent-link"

  Scenario: agent-link finishes loading its connections
    Given the dashboard is visible
    And the dashboard shows "agent-link"
    When I open "agent-link" from the dashboard
    Then agent-link is visible in the dashboard
    When I select the "Connections" tab
    Then the agent-link connection list finishes loading
