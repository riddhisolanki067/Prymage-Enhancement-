(() => {
  // ../prymage/prymage/public/js/prymage.bundle.js
  frappe.provide("prymage");
  prymage.run_safely = function(label, fn) {
    try {
      fn();
    } catch (e) {
      console.error("prymage: " + label + " failed", e);
    }
  };
  $(document).on("app_ready", function() {
    prymage.run_safely("subscription notice", prymage.show_subscription_notice);
    if (frappe.router && frappe.router.on) {
      frappe.router.on("change", function() {
        prymage.run_safely("company selection", prymage.enforce_company_selection);
        prymage.run_safely("active company pill", prymage.show_active_company);
      });
    }
  });
  prymage.show_subscription_notice = function() {
    const info = frappe.boot && frappe.boot.prymage_subscription;
    if (!info || !info.message)
      return;
    const key = "prymage_subscription_notice:" + (info.expiry_date || "") + ":" + frappe.session.user;
    if (sessionStorage.getItem(key))
      return;
    sessionStorage.setItem(key, "1");
    const titles = {
      expired: __("Subscription Expired"),
      critical: __("Subscription Expiring"),
      warning: __("Subscription Expiring"),
      info: __("Subscription Reminder")
    };
    const indicators = { expired: "red", critical: "red", warning: "orange", info: "blue" };
    let message = frappe.utils.escape_html(info.message);
    if (info.provider_contact) {
      message += '<br><br><span class="text-muted">' + __("Contact: {0}", [frappe.utils.escape_html(info.provider_contact)]) + "</span>";
    }
    if (info.expired && info.is_admin && info.restricted) {
      message += '<br><br><b class="text-danger">' + __("Everyone except the Administrator is being stopped at login.") + "</b>";
    }
    if (info.bypassed) {
      message += '<br><br><span class="text-muted">' + __("The access restriction is released by prymage_subscription_bypass in site_config.json.") + "</span>";
    }
    if (info.severity === "expired" || info.severity === "critical") {
      frappe.msgprint({
        title: titles[info.severity],
        message,
        indicator: indicators[info.severity]
      });
    } else {
      frappe.show_alert(
        { message, indicator: indicators[info.severity] || "blue" },
        15
      );
    }
  };
  prymage.enforce_company_selection = function() {
    const info = frappe.boot && frappe.boot.prymage_multi_company;
    if (!info || !info.selection_pending)
      return;
    const route = frappe.router && frappe.router.current_route || [];
    if (route[0] === "company-selection")
      return;
    setTimeout(function() {
      prymage.run_safely("company selection redirect", function() {
        frappe.set_route("company-selection");
      });
    }, 0);
  };
  prymage.show_active_company = function() {
    const info = frappe.boot && frappe.boot.prymage_multi_company;
    if (!info || !info.require_selection)
      return;
    if (!info.selected_companies || !info.selected_companies.length)
      return;
    if (document.querySelector(".prymage-active-company"))
      return;
    const label = info.selected_companies.length > 1 ? info.selected_companies[0] + " +" + (info.selected_companies.length - 1) : info.selected_companies[0];
    const $pill = $(
      '<a class="prymage-active-company" href="/app/company-selection"><span class="indicator-pill blue">' + frappe.utils.escape_html(label) + "</span></a>"
    ).attr("title", __("Switch company")).css({ display: "flex", "align-items": "center", "margin-right": "8px" });
    const containers = [
      [".body-sidebar-bottom", "prepend"],
      [".navbar > .flex", "prepend"],
      [".navbar", "append"]
    ];
    for (const [selector, how] of containers) {
      const $target = $(selector).first();
      if ($target.length) {
        how === "prepend" ? $target.prepend($pill) : $target.append($pill);
        return;
      }
    }
  };
})();
//# sourceMappingURL=prymage.bundle.Q3KQ7BQY.js.map
