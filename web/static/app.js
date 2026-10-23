/* 网络打印服务前端脚本：上传、参数联动、队列与日志轮询。 */

(function () {
  "use strict";

  var TASK_INTERVAL = 1500;
  var LOG_INTERVAL = 10000;

  var state = {
    files: [],
    paperSignature: "",
    taskSignature: "",
    logSignature: "",
    submitting: false
  };

  function $(id) {
    return document.getElementById(id);
  }

  function anyModalOpen() {
    var modals = document.querySelectorAll(".modal");
    for (var i = 0; i < modals.length; i++) {
      if (!modals[i].classList.contains("hidden")) {
        return true;
      }
    }
    return false;
  }

  function toast(message, isError) {
    var box = $("toast");
    box.textContent = message;
    box.className = "toast show" + (isError ? " error" : "");
    window.clearTimeout(box._timer);
    box._timer = window.setTimeout(function () {
      box.className = "toast";
    }, 3200);
  }

  function formatSize(bytes) {
    var value = Number(bytes || 0);
    if (value < 1024) {
      return value + " B";
    }
    var units = ["KB", "MB", "GB"];
    for (var i = 0; i < units.length; i++) {
      value = value / 1024;
      if (value < 1024 || i === units.length - 1) {
        return value.toFixed(1) + " " + units[i];
      }
    }
    return value + " B";
  }

  function request(url, options) {
    return fetch(url, options).then(function (response) {
      var needLogin = response.status === 401;
      return response.json().catch(function () {
        return { ok: false, error: "服务响应异常（HTTP " + response.status + "）" };
      }).then(function (data) {
        if (needLogin || data.need_login) {
          window.location.href = "/login";
        }
        if (!response.ok && data.error === undefined && data.message === undefined) {
          data.error = "请求失败（HTTP " + response.status + "）";
        }
        return data;
      });
    });
  }

  /* ------------------------------------------------------------ 打印机与纸张 */

  function loadPrinters() {
    return request("/api/printers").then(function (data) {
      var select = $("printer");
      var current = select.value;
      select.innerHTML = "";

      var printers = data.printers || [];
      var preferred = window.JY_DEFAULT_PRINTER || data.default || "";
      var matched = false;

      var auto = document.createElement("option");
      auto.value = "";
      auto.textContent = data.default ? ("系统默认（" + data.default + "）") : "系统默认打印机";
      select.appendChild(auto);

      printers.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.name;
        option.textContent = item.name + (item.is_default ? "（默认）" : "");
        select.appendChild(option);
        if (item.name === current || item.name === preferred) {
          matched = true;
        }
      });

      if (current && Array.prototype.some.call(select.options, function (o) { return o.value === current; })) {
        select.value = current;
      } else if (matched) {
        select.value = preferred;
      }

      var note = $("printer-note");
      if (!printers.length) {
        note.textContent = "未检测到可用打印机：请确认打印机已安装、开机，并已关闭虚拟打印机过滤。";
        note.className = "note error";
      } else {
        note.textContent = "共检测到 " + printers.length + " 台可用打印机；虚拟打印机已自动过滤。";
        note.className = "note";
      }
      return loadCapabilities(select.value, true);
    });
  }

  function loadCapabilities(printerName, resetPaper) {
    var url = "/api/printer/capabilities" + (printerName ? "?name=" + encodeURIComponent(printerName) : "");
    return request(url).then(function (data) {
      var select = $("paper");
      var papers = data.papers || [];
      var signature = JSON.stringify(papers);
      var keep = select.value || window.JY_DEFAULT_PAPER || "A4";

      // 纸张列表未变化时不做无意义的 DOM 重排，避免下拉框被反复重建
      if (signature === state.paperSignature && !resetPaper) {
        return;
      }
      state.paperSignature = signature;

      select.innerHTML = "";
      papers.forEach(function (paper) {
        var option = document.createElement("option");
        option.value = paper.name;
        option.textContent = paper.name;
        option.dataset.id = paper.id;
        select.appendChild(option);
      });

      var exists = Array.prototype.some.call(select.options, function (o) { return o.value === keep; });
      if (exists) {
        select.value = keep;
      } else if (select.options.length) {
        select.value = select.options[0].value;
      }

      var paperNote = $("paper-note");
      if (paperNote) {
        // 驱动未上报具体纸张尺寸时，后端会给出 paper_note，这里如实提示用户
        paperNote.textContent = data.paper_note ? "纸张说明：" + data.paper_note : "";
        paperNote.hidden = !data.paper_note;
      }

      var duplex = $("duplex");
      var wantDuplex = window.JY_DEFAULT_DUPLEX === "vertical";
      duplex.value = (data.duplex || wantDuplex) ? (wantDuplex ? "vertical" : "simplex") : "simplex";
      duplex.options[1].disabled = !data.duplex;
      if (!data.duplex) {
        duplex.value = "simplex";
      }
    });
  }

  /* ------------------------------------------------------------ 文件选择 */

  function addFiles(fileList) {
    Array.prototype.forEach.call(fileList || [], function (file) {
      var duplicated = state.files.some(function (item) {
        return item.name === file.name && item.size === file.size && item.lastModified === file.lastModified;
      });
      if (!duplicated) {
        state.files.push(file);
      }
    });
    renderFiles();
  }

  function renderFiles() {
    var list = $("file-list");
    list.innerHTML = "";
    state.files.forEach(function (file, index) {
      var row = document.createElement("li");

      var name = document.createElement("span");
      name.className = "name";
      name.textContent = file.name;

      var meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = formatSize(file.size);

      var remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "移除";
      remove.addEventListener("click", function () {
        state.files.splice(index, 1);
        renderFiles();
      });

      row.appendChild(name);
      row.appendChild(meta);
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  // 页码范围格式校验（与后端 core.render.parse_page_range 保持一致）
  function isPageRangeValid(text) {
    var cleaned = String(text || "").replace(/，/g, ",").replace(/[－—]/g, "-").trim();
    if (!cleaned) {
      return true;
    }
    var chunks = cleaned.split(",");
    for (var i = 0; i < chunks.length; i++) {
      var item = chunks[i].trim();
      if (!item) {
        continue;
      }
      var matched = item.match(/^(\d*)-(\d*)$/);
      if (matched) {
        if (!matched[1] && !matched[2]) {
          return false;
        }
        var start = matched[1] ? parseInt(matched[1], 10) : 1;
        var end = matched[2] ? parseInt(matched[2], 10) : null;
        if (start < 1 || (end !== null && (end < 1 || end < start))) {
          return false;
        }
        continue;
      }
      if (!/^\d+$/.test(item) || parseInt(item, 10) < 1) {
        return false;
      }
    }
    return true;
  }

  // 「比例 %」只在自定义缩放下可填
  function syncScalePercent() {
    var custom = $("scale").value === "custom";
    var input = $("scale-percent");
    input.disabled = !custom;
    input.style.opacity = custom ? "1" : "0.5";
  }

  // 过滤空项后拼接展示文本（兼容旧日志中缺失的新列）
  function joinParams(parts) {
    return parts
      .filter(function (item) {
        return item !== undefined && item !== null && String(item).trim() !== "";
      })
      .join(" / ");
  }

  function submitTasks() {
    if (state.submitting) {
      return;
    }
    if (!state.files.length) {
      toast("请先选择要打印的文件", true);
      return;
    }

    var pageRange = ($("page-range").value || "").trim();
    if (pageRange && !isPageRangeValid(pageRange)) {
      toast("页面范围格式不正确，请填写如 1-3,5 或 2-", true);
      return;
    }

    var form = new FormData();
    state.files.forEach(function (file) {
      form.append("files", file, file.name);
    });
    form.append("printer", $("printer").value);
    form.append("paper", $("paper").value);
    form.append("duplex", $("duplex").value);
    form.append("copies", $("copies").value || "1");
    form.append("page_range", pageRange);
    form.append("scale", $("scale").value);
    form.append("scale_percent", $("scale-percent").value || "100");
    form.append("color", $("color").value);
    form.append("quality", $("quality").value);
    form.append("require_confirmation", $("require-confirmation").checked ? "1" : "0");

    state.submitting = true;
    $("submit").disabled = true;

    request("/api/upload", { method: "POST", body: form })
      .then(function (data) {
        var accepted = (data.tasks || []).length;
        var rejected = data.rejected || [];
        if (accepted) {
          toast("已提交 " + accepted + " 个打印任务");
          state.files = [];
          renderFiles();
        }
        if (rejected.length) {
          var detail = rejected.map(function (item) {
            return item.filename + "：" + item.reason;
          }).join("；");
          toast("有 " + rejected.length + " 个文件未提交，" + detail, true);
        } else if (!accepted && data.error) {
          toast(data.error, true);
        }
        forceRefresh();
      })
      .catch(function (error) {
        toast("提交失败：" + error.message, true);
      })
      .finally(function () {
        state.submitting = false;
        $("submit").disabled = false;
      });
  }

  /* ------------------------------------------------------------ 队列与日志 */

  function badge(status, text) {
    var span = document.createElement("span");
    span.className = "badge " + status;
    span.textContent = text;
    return span;
  }

  function cell(text, className) {
    var td = document.createElement("td");
    td.textContent = text === undefined || text === null ? "" : text;
    if (className) {
      td.className = className;
    }
    return td;
  }

  function refreshTasks() {
    return request("/api/tasks?limit=100").then(function (data) {
      var tasks = data.tasks || [];
      // 数据未变化时不重建 DOM，避免用户点击瞬间按钮节点被替换而丢失操作
      var signature = tasks
        .map(function (task) {
          return task.id + task.status + task.pages + task.message + task.printer;
        })
        .join("|");
      if (anyModalOpen()) {
        // 弹窗打开期间不重建表格，避免干扰操作；关闭后下一轮会自动刷新
        return;
      }
      if (signature === state.taskSignature) {
        return;
      }
      state.taskSignature = signature;

      var body = $("task-body");
      body.innerHTML = "";

      if (!tasks.length) {
        var empty = document.createElement("tr");
        empty.className = "empty";
        var td = document.createElement("td");
        td.colSpan = 7;
        td.textContent = "暂无打印任务";
        empty.appendChild(td);
        body.appendChild(empty);
        return;
      }

      tasks.forEach(function (task) {
        var row = document.createElement("tr");
        row.appendChild(cell(task.filename));
        row.appendChild(cell(task.size_text, "mono"));
        row.appendChild(cell(task.params_text + (task.printer ? " / " + task.printer : "")));
        row.appendChild(cell((task.client_host || "") + (task.client_ip ? " (" + task.client_ip + ")" : ""), "msg"));

        var statusCell = document.createElement("td");
        statusCell.appendChild(badge(task.status, task.status_text));
        row.appendChild(statusCell);

        row.appendChild(cell(task.created_at, "mono"));

        var actionCell = document.createElement("td");

        var previewButton = document.createElement("button");
        previewButton.type = "button";
        previewButton.className = "link";
        previewButton.textContent = "预览";
        previewButton.addEventListener("click", function () {
          openPreview(task);
        });
        actionCell.appendChild(previewButton);

        if (task.status === "review") {
          var confirmButton = document.createElement("button");
          confirmButton.type = "button";
          confirmButton.className = "link";
          confirmButton.textContent = "确认打印";
          confirmButton.addEventListener("click", function () {
            act("/api/tasks/" + task.id + "/print", "已提交打印");
          });
          actionCell.appendChild(confirmButton);
        }

        if (task.status === "queued" || task.status === "review") {
          var cancel = document.createElement("button");
          cancel.type = "button";
          cancel.className = "link";
          cancel.textContent = "取消";
          cancel.addEventListener("click", function () {
            act("/api/tasks/" + task.id + "/cancel", "已取消任务");
          });
          actionCell.appendChild(cancel);
        } else if (task.status === "failed" || task.status === "canceled") {
          var retry = document.createElement("button");
          retry.type = "button";
          retry.className = "link";
          retry.textContent = "重试";
          retry.addEventListener("click", function () {
            act("/api/tasks/" + task.id + "/retry", "已重新排队");
          });
          actionCell.appendChild(retry);
        }

        if (task.message && (task.status === "failed" || task.status === "printing")) {
          row.title = task.message;
        }
        row.appendChild(actionCell);
        body.appendChild(row);
      });
    }).catch(function () {
      /* 轮询失败保持静默，等待下一次重试 */
    });
  }

  function act(url, successText) {
    request(url, { method: "POST" }).then(function (data) {
      if (data.ok) {
        toast(successText);
      } else {
        toast(data.message || data.error || "操作失败", true);
      }
      forceRefresh();
    }).catch(function (error) {
      toast("操作失败：" + error.message, true);
    });
  }

  function refreshLogs() {
    return request("/api/logs?limit=50").then(function (data) {
      var logs = data.logs || [];
      var signature = logs
        .map(function (log) {
          return log.time + log.filename + log.status + log.message;
        })
        .join("|");
      if (anyModalOpen() || signature === state.logSignature) {
        return;
      }
      state.logSignature = signature;

      var body = $("log-body");
      body.innerHTML = "";

      if (!logs.length) {
        var empty = document.createElement("tr");
        empty.className = "empty";
        var td = document.createElement("td");
        td.colSpan = 7;
        td.textContent = "暂无日志";
        empty.appendChild(td);
        body.appendChild(empty);
        return;
      }

      logs.forEach(function (log) {
        var row = document.createElement("tr");
        row.appendChild(cell(log.time, "mono"));
        row.appendChild(cell((log.client_host || "") + (log.client_ip ? " (" + log.client_ip + ")" : ""), "msg"));
        row.appendChild(cell(log.filename));
        row.appendChild(cell(joinParams([
          log.copies ? log.copies + " 份" : "",
          log.duplex,
          log.paper,
          log.pages_range,
          log.scale,
          log.color,
          log.quality
        ])));
        row.appendChild(cell(log.printer));
        row.appendChild(cell(log.status));
        row.appendChild(cell(log.message, "msg"));
        body.appendChild(row);
      });
    }).catch(function () {
      /* 轮询失败保持静默 */
    });
  }

  /* ------------------------------------------------------------ 文档预览 */

  function appendPreviewEmpty(text) {
    var empty = document.createElement("div");
    empty.className = "preview-empty";
    empty.textContent = text;
    $("preview-grid").appendChild(empty);
  }

  function closePreview() {
    $("preview-modal").classList.add("hidden");
    $("preview-grid").innerHTML = "";
  }

  function openPreview(task) {
    var modal = $("preview-modal");
    var grid = $("preview-grid");
    $("preview-title").textContent = "文档预览：" + task.filename;
    $("preview-meta").textContent = "正在生成缩略图，文件较大时请稍候……";
    grid.innerHTML = "";
    appendPreviewEmpty("加载中…");
    modal.classList.remove("hidden");

    request("/api/preview/" + task.id).then(function (data) {
      if (!data.ok) {
        grid.innerHTML = "";
        $("preview-meta").textContent = "";
        appendPreviewEmpty(data.error || "预览生成失败");
        return;
      }

      var pages = data.pages || [];
      var meta = "共 " + data.page_count + " 页";
      if (data.rendered < data.page_count) {
        meta += "（仅展示前 " + data.rendered + " 页缩略图）";
      }
      meta += " ｜ " + (task.params_text || "") + (task.printer ? " / " + task.printer : "");
      $("preview-meta").textContent = meta;

      grid.innerHTML = "";
      if (!pages.length) {
        appendPreviewEmpty("该文件没有可预览的页面");
        return;
      }
      pages.forEach(function (page) {
        var item = document.createElement("div");
        item.className = "preview-item";

        var img = document.createElement("img");
        img.src = page.url;
        img.alt = "第 " + page.index + " 页";
        img.loading = "lazy";

        var label = document.createElement("span");
        label.textContent = "第 " + page.index + " 页";

        item.appendChild(img);
        item.appendChild(label);
        grid.appendChild(item);
      });
    }).catch(function (error) {
      grid.innerHTML = "";
      $("preview-meta").textContent = "";
      appendPreviewEmpty("预览失败：" + error.message);
    });
  }

  /* ------------------------------------------------------------ 账号相关 */

  function openModal(id) {
    $(id).classList.remove("hidden");
  }

  function closeModal(id) {
    $(id).classList.add("hidden");
    if (id === "preview-modal") {
      $("preview-grid").innerHTML = "";
    }
  }

  function bindModalDismiss() {
    // 所有弹窗统一支持：点遮罩关闭、按 Esc 关闭，避免出现「关不掉」的窗口
    var modals = document.querySelectorAll(".modal");
    var index;
    var bindBackdrop = function (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) {
          closeModal(modal.id);
        }
      });
    };
    for (index = 0; index < modals.length; index++) {
      bindBackdrop(modals[index]);
    }
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") {
        return;
      }
      var current = document.querySelectorAll(".modal");
      for (var i = 0; i < current.length; i++) {
        if (!current[i].classList.contains("hidden")) {
          closeModal(current[i].id);
        }
      }
    });
  }

  function forceRefresh() {
    // 清空签名以强制重建表格（用于操作后立即刷新）
    state.taskSignature = "";
    state.logSignature = "";
    refreshTasks();
    refreshLogs();
  }

  function submitPassword() {
    var form = new FormData();
    form.append("old_password", $("old-password").value);
    form.append("new_password", $("new-password").value);
    request("/api/password", { method: "POST", body: form }).then(function (data) {
      toast(data.ok ? (data.message || "密码已更新") : (data.message || "修改失败"), !data.ok);
      if (data.ok) {
        $("old-password").value = "";
        $("new-password").value = "";
        closeModal("password-modal");
      }
    }).catch(function (error) {
      toast("修改失败：" + error.message, true);
    });
  }

  function loadUsers() {
    if (!window.JY_IS_ADMIN) {
      return;
    }
    request("/api/users").then(function (data) {
      var body = $("users-body");
      body.innerHTML = "";
      var list = data.users || [];
      if (!list.length) {
        var empty = document.createElement("tr");
        empty.className = "empty";
        var td = document.createElement("td");
        td.colSpan = 4;
        td.textContent = "暂无账号";
        empty.appendChild(td);
        body.appendChild(empty);
        return;
      }

      list.forEach(function (item) {
        var row = document.createElement("tr");
        row.appendChild(cell(item.username));
        row.appendChild(cell(item.role_text));
        row.appendChild(cell(item.last_login || "—", "mono"));

        var actions = document.createElement("td");

        var reset = document.createElement("button");
        reset.type = "button";
        reset.className = "link";
        reset.textContent = "重置密码";
        reset.addEventListener("click", function () {
          var password = window.prompt("为「" + item.username + "」设置新密码（至少 6 位）");
          if (!password) {
            return;
          }
          var form = new FormData();
          form.append("password", password);
          request("/api/users/" + encodeURIComponent(item.username) + "/password", {
            method: "POST",
            body: form
          }).then(function (res) {
            toast(res.ok ? "密码已重置" : (res.message || "重置失败"), !res.ok);
            loadUsers();
          });
        });
        actions.appendChild(reset);

        if (item.username !== window.JY_USERNAME) {
          var remove = document.createElement("button");
          remove.type = "button";
          remove.className = "link";
          remove.textContent = "删除";
          remove.addEventListener("click", function () {
            if (!window.confirm("确认删除账号「" + item.username + "」？")) {
              return;
            }
            request("/api/users/" + encodeURIComponent(item.username), { method: "DELETE" }).then(function (res) {
              toast(res.ok ? "账号已删除" : (res.message || "删除失败"), !res.ok);
              loadUsers();
            });
          });
          actions.appendChild(remove);
        }

        row.appendChild(actions);
        body.appendChild(row);
      });
    });
  }

  function createUser() {
    var form = new FormData();
    form.append("username", $("new-username").value);
    form.append("password", $("new-user-password").value);
    form.append("role", $("new-user-role").value);
    request("/api/users", { method: "POST", body: form }).then(function (data) {
      toast(data.ok ? (data.message || "账号已创建") : (data.message || "创建失败"), !data.ok);
      if (data.ok) {
        $("new-username").value = "";
        $("new-user-password").value = "";
        loadUsers();
      }
    });
  }

  function openQr() {
    var urls = window.JY_ACCESS_URLS || [];
    var target = "";
    for (var i = 0; i < urls.length; i++) {
      if (urls[i].indexOf("127.0.0.1") === -1 && urls[i].indexOf("localhost") === -1) {
        target = urls[i];
        break;
      }
    }
    if (!target) {
      target = urls[0] || window.location.origin;
    }
    $("qr-image").src = "/api/qrcode?url=" + encodeURIComponent(target);
    $("qr-url").textContent = target;
    openModal("qr-modal");
  }

  /* ------------------------------------------------------------ 初始化 */

  function bindEvents() {
    var dropzone = $("dropzone");
    var input = $("file-input");

    $("scale").addEventListener("change", syncScalePercent);

    dropzone.addEventListener("click", function () {
      input.click();
    });
    input.addEventListener("change", function () {
      addFiles(input.files);
      input.value = "";
    });

    ["dragenter", "dragover"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.add("dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      dropzone.addEventListener(name, function (event) {
        event.preventDefault();
        dropzone.classList.remove("dragover");
      });
    });
    dropzone.addEventListener("drop", function (event) {
      if (event.dataTransfer && event.dataTransfer.files) {
        addFiles(event.dataTransfer.files);
      }
    });

    $("printer").addEventListener("change", function () {
      loadCapabilities($("printer").value, true);
    });
    $("submit").addEventListener("click", submitTasks);
    $("preview-close").addEventListener("click", closePreview);
    bindModalDismiss();

    $("change-password").addEventListener("click", function () {
      openModal("password-modal");
    });
    $("password-close").addEventListener("click", function () {
      closeModal("password-modal");
    });
    $("password-submit").addEventListener("click", submitPassword);

    if (window.JY_IS_ADMIN) {
      $("manage-users").addEventListener("click", function () {
        openModal("users-modal");
        loadUsers();
      });
      $("users-close").addEventListener("click", function () {
        closeModal("users-modal");
      });
      $("create-user").addEventListener("click", createUser);
    }

    if (window.JY_QR_AVAILABLE) {
      $("show-qrcode").addEventListener("click", openQr);
      $("qr-close").addEventListener("click", function () {
        closeModal("qr-modal");
      });
    }
    $("refresh").addEventListener("click", function () {
      forceRefresh();
      loadPrinters();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindEvents();
    $("duplex").value = window.JY_DEFAULT_DUPLEX === "vertical" ? "vertical" : "simplex";
    $("page-range").value = window.JY_DEFAULT_PAGE_RANGE || "";
    $("scale").value = window.JY_DEFAULT_SCALE || "fit";
    $("scale-percent").value = window.JY_DEFAULT_SCALE_PERCENT || 100;
    $("color").value = window.JY_DEFAULT_COLOR || "color";
    $("quality").value = window.JY_DEFAULT_QUALITY || "normal";
    syncScalePercent();
    $("require-confirmation").checked = !!window.JY_REQUIRE_CONFIRMATION;

    var infoCard = $("info-card");
    if (infoCard) {
      if (window.localStorage.getItem("jy-info-open") === "1") {
        infoCard.open = true;
      }
      infoCard.addEventListener("toggle", function () {
        window.localStorage.setItem("jy-info-open", infoCard.open ? "1" : "0");
      });
    }
    loadPrinters();
    refreshTasks();
    refreshLogs();
    window.setInterval(refreshTasks, TASK_INTERVAL);
    window.setInterval(refreshLogs, LOG_INTERVAL);

    request("/api/me").then(function (data) {
      if (data.ok && data.default_password) {
        toast("当前账号仍在使用初始密码，请立即修改", true);
      }
    });
  });
})();
