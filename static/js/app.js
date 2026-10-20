(() => {
  "use strict";

  const dismissFlash = (flash) => {
    if (!flash || flash.classList.contains("is-leaving")) {
      return;
    }
    flash.classList.add("is-leaving");
    window.setTimeout(() => flash.remove(), 260);
  };

  const formatMediaDuration = (seconds) => {
    if (!Number.isFinite(seconds) || seconds <= 0) {
      return "--:--";
    }
    const total = Math.round(seconds);
    const pad = (value) => String(value).padStart(2, "0");
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor(total / 60) % 60;
    const remaining = total % 60;
    return hours > 0
      ? `${hours}:${pad(minutes)}:${pad(remaining)}`
      : `${pad(minutes)}:${pad(remaining)}`;
  };

  document.querySelectorAll("[data-flash]").forEach((flash) => {
    const closeButton = flash.querySelector("[data-flash-close]");
    closeButton?.addEventListener("click", () => dismissFlash(flash));
    window.setTimeout(() => dismissFlash(flash), 6500);
  });

  document.querySelectorAll("[data-auth-tabs]").forEach((tabList) => {
    const card = tabList.closest(".auth-card") || document;
    const tabs = Array.from(tabList.querySelectorAll("[data-auth-tab]"));
    const panels = Array.from(card.querySelectorAll("[data-auth-panel]"));
    const methodInput = card.querySelector("#login-method");

    if (!tabs.length || !panels.length) {
      return;
    }

    const activate = (method, moveFocus = false) => {
      tabs.forEach((tab) => {
        const active = tab.dataset.authTab === method;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.tabIndex = active ? 0 : -1;
      });

      panels.forEach((panel) => {
        const active = panel.dataset.authPanel === method;
        panel.hidden = !active;
        if (active && moveFocus) {
          panel.querySelector("input")?.focus();
        }
      });

      card.querySelectorAll("[data-auth-switch-for]").forEach((element) => {
        element.hidden = element.dataset.authSwitchFor !== method;
      });

      if (methodInput) {
        methodInput.value = method;
      }
    };

    tabs.forEach((tab) => {
      tab.addEventListener("click", () => activate(tab.dataset.authTab, true));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
          return;
        }
        event.preventDefault();
        const current = tabs.indexOf(tab);
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const nextTab = tabs[(current + direction + tabs.length) % tabs.length];
        nextTab.focus();
        activate(nextTab.dataset.authTab);
      });
    });

    activate(
      tabs.find((tab) => tab.classList.contains("is-active"))?.dataset.authTab ||
        tabs[0].dataset.authTab,
    );
  });

  document.querySelectorAll("[data-send-code-url]").forEach((button) => {
    const form = button.closest("form");
    const phoneInput = form?.querySelector('input[name="phone"]');
    const codeInput = form?.querySelector('input[name="code"]');
    const status = form?.querySelector("[data-sms-status]");
    const csrfInput = form?.querySelector('input[name="csrf_token"]');
    let cooldownTimer = null;

    if (!form || !phoneInput || !csrfInput) {
      return;
    }

    const setStatus = (message, kind = "") => {
      if (!status) {
        return;
      }
      status.textContent = message;
      status.classList.remove("is-error", "is-success");
      if (kind) {
        status.classList.add(`is-${kind}`);
      }
    };

    const startCooldown = (seconds) => {
      if (cooldownTimer) {
        window.clearInterval(cooldownTimer);
      }
      let remaining = Math.max(1, Math.ceil(Number(seconds) || 60));

      const render = () => {
        if (remaining <= 0) {
          window.clearInterval(cooldownTimer);
          cooldownTimer = null;
          button.disabled = false;
          button.textContent = "重新获取";
          return;
        }
        button.disabled = true;
        button.textContent =
          remaining >= 120
            ? `${Math.ceil(remaining / 60)} 分钟后重试`
            : `${remaining} 秒后重试`;
        remaining -= 1;
      };

      render();
      cooldownTimer = window.setInterval(render, 1000);
    };

    button.addEventListener("click", async () => {
      const phone = phoneInput.value.trim();
      if (!/^1[3-9]\d{9}$/.test(phone)) {
        setStatus("请输入有效的 11 位手机号。", "error");
        phoneInput.focus();
        return;
      }

      const originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = "发送中…";
      setStatus("正在发送验证码…");

      const data = new FormData();
      data.append("csrf_token", csrfInput.value);
      data.append("phone", phone);

      try {
        const response = await fetch(button.dataset.sendCodeUrl, {
          method: "POST",
          body: data,
          headers: { Accept: "application/json" },
        });
        let payload = null;
        try {
          payload = await response.json();
        } catch {
          payload = null;
        }

        if (!response.ok || !payload?.ok) {
          setStatus(
            payload?.message || "验证码发送失败，请稍后重试。",
            "error",
          );
          if (payload?.retry_after) {
            startCooldown(payload.retry_after);
          } else {
            button.disabled = false;
            button.textContent = "重新获取";
          }
          return;
        }

        setStatus(payload.message || "验证码已发送。", "success");
        startCooldown(payload.retry_after || 60);
        codeInput?.focus();
      } catch {
        setStatus("网络异常，请稍后再试。", "error");
        button.disabled = false;
        button.textContent = originalLabel;
      }
    });
  });

  document.querySelectorAll("[data-password-toggle]").forEach((button) => {
    const input = document.getElementById(button.dataset.passwordToggle);
    if (!input) {
      return;
    }

    button.addEventListener("click", () => {
      const isPassword = input.type === "password";
      input.type = isPassword ? "text" : "password";
      button.textContent = isPassword ? "隐藏" : "显示";
      button.setAttribute("aria-label", isPassword ? "隐藏密码" : "显示密码");
      input.focus();
    });
  });

  document.querySelectorAll("[data-media-input]").forEach((input) => {
    const form = input.closest("form");
    const preview = document.getElementById(input.dataset.previewTarget);
    const grid = preview?.querySelector("[data-media-preview-grid]");
    const fileCount = preview?.querySelector("[data-media-filecount]");
    const clearButton = preview?.querySelector("[data-media-clear]");
    const status = form?.querySelector("[data-media-status]");
    const submitButton = form?.querySelector('button[type="submit"]');
    const uploadedContainer = form?.querySelector("[data-uploaded-media]");
    const uploadUrl = input.dataset.uploadUrl;
    const discardUrl = input.dataset.discardUrl;
    const csrfToken =
      form?.querySelector('input[name="csrf_token"]')?.value || "";

    if (!form || !preview || !grid || !uploadUrl || !discardUrl) {
      return;
    }

    const maxFiles = Number(input.dataset.maxFiles) || 6;
    const maxImageBytes =
      Number(input.dataset.maxImageBytes) || 10 * 1024 * 1024;
    const maxVideoBytes =
      Number(input.dataset.maxVideoBytes) || 300 * 1024 * 1024;
    const maxTotalBytes = Number(input.dataset.maxTotalBytes) || 0;
    const imageExtensions = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp"]);
    const videoExtensions = new Set([".mp4", ".m4v", ".mov", ".webm", ".ogv"]);
    const items = [];
    let nextId = 1;

    try {
      const probe = new DataTransfer();
      input.files = probe.files;
    } catch {
      return;
    }

    const formatSize = (bytes) => {
      if (bytes >= 1024 * 1024) {
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
      }
      return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    };

    const extensionOf = (name) => {
      const dot = name.lastIndexOf(".");
      return dot >= 0 ? name.slice(dot).toLowerCase() : "";
    };

    const fileKey = (file) => `${file.name}|${file.size}|${file.lastModified}`;

    const setStatus = (message, kind = "") => {
      if (!status) {
        return;
      }
      status.textContent = message;
      status.classList.remove("is-error", "is-success");
      if (kind) {
        status.classList.add(`is-${kind}`);
      }
    };

    const clearNativeInput = () => {
      const transfer = new DataTransfer();
      input.files = transfer.files;
    };

    const validateFile = (file) => {
      const extension = extensionOf(file.name);
      const kind = imageExtensions.has(extension)
        ? "image"
        : videoExtensions.has(extension)
          ? "video"
          : null;

      if (!kind) {
        return { error: `“${file.name}”的格式不受支持。` };
      }

      const limit = kind === "image" ? maxImageBytes : maxVideoBytes;
      if (file.size > limit) {
        return {
          error: `“${file.name}”超过${kind === "image" ? "图片" : "视频"}大小限制（${formatSize(limit)}）。`,
        };
      }

      return { kind };
    };

    const updateSubmitState = () => {
      if (!submitButton) {
        return;
      }
      const uploading = items.some((item) => item.status === "uploading");
      if (!submitButton.dataset.idleLabel) {
        submitButton.dataset.idleLabel = submitButton.textContent;
      }
      submitButton.disabled = uploading;
      submitButton.textContent = uploading
        ? "文件上传中…"
        : submitButton.dataset.idleLabel;
    };

    const updateItemProgress = (item) => {
      if (!item.progressEl) {
        return;
      }
      if (item.status === "error") {
        item.progressEl.classList.add("is-error");
        item.progressText.textContent = "上传失败";
        item.progressBar.style.width = "100%";
        return;
      }
      if (item.status === "done") {
        item.progressEl.remove();
        item.progressEl = null;
        item.progressText = null;
        item.progressBar = null;
        return;
      }
      item.progressText.textContent = `${item.progress}%`;
      item.progressBar.style.width = `${item.progress}%`;
    };

    const refreshSummary = () => {
      const uploading = items.filter((item) => item.status === "uploading");
      const percent = uploading.length
        ? Math.round(
            uploading.reduce((total, item) => total + item.progress, 0) /
              uploading.length,
          )
        : 0;

      if (fileCount) {
        fileCount.textContent = uploading.length
          ? `已选择 ${items.length} 个文件 · 上传中 ${percent}%`
          : `已选择 ${items.length} 个文件`;
      }

      if (uploading.length) {
        setStatus(`正在上传 ${uploading.length} 个文件… ${percent}%`);
      } else if (items.some((item) => item.status === "error")) {
        setStatus("有文件上传失败，请移除后重新选择。", "error");
      } else if (items.length) {
        setStatus("文件已上传，可以发布。", "success");
      }

      updateSubmitState();
    };

    const discardToken = (token) => {
      const data = new FormData();
      data.append("csrf_token", csrfToken);
      data.append("token", token);
      fetch(discardUrl, {
        method: "POST",
        body: data,
        keepalive: true,
      }).catch(() => {});
    };

    const removeItem = (item) => {
      if (item.xhr) {
        item.xhr.abort();
        item.xhr = null;
      }
      if (item.token) {
        discardToken(item.token);
      }
      if (item.objectUrl) {
        URL.revokeObjectURL(item.objectUrl);
      }
      item.hidden?.remove();
      const index = items.indexOf(item);
      if (index >= 0) {
        items.splice(index, 1);
      }
      render();
    };

    const failUpload = (item, message) => {
      item.status = "error";
      item.progress = 0;
      item.xhr = null;
      updateItemProgress(item);
      refreshSummary();
      setStatus(message, "error");
    };

    const startUpload = (item) => {
      const xhr = new XMLHttpRequest();
      item.xhr = xhr;
      xhr.open("POST", uploadUrl);
      xhr.responseType = "json";

      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) {
          return;
        }
        item.progress = Math.min(
          99,
          Math.round((event.loaded / event.total) * 100),
        );
        updateItemProgress(item);
        refreshSummary();
      });

      xhr.addEventListener("load", () => {
        const payload = xhr.response;
        if (xhr.status >= 200 && xhr.status < 300 && payload?.ok) {
          item.status = "done";
          item.progress = 100;
          item.token = payload.token;
          item.url = payload.url;

          const hidden = document.createElement("input");
          hidden.type = "hidden";
          hidden.name = "uploaded_media";
          hidden.value = payload.token;
          hidden.dataset.kind = payload.kind;
          hidden.dataset.url = payload.url;
          uploadedContainer?.append(hidden);
          item.hidden = hidden;

          updateItemProgress(item);
          refreshSummary();
        } else {
          failUpload(item, payload?.message || "上传失败，请稍后重试。");
        }
      });

      xhr.addEventListener("error", () => {
        failUpload(item, "网络错误，上传失败，请稍后重试。");
      });

      const data = new FormData();
      data.append("csrf_token", csrfToken);
      data.append("media", item.file, item.file.name);
      xhr.send(data);
    };

    const render = () => {
      grid.replaceChildren();

      items.forEach((item) => {
        const sourceUrl = item.file ? item.objectUrl : item.url;
        const itemEl = document.createElement("div");
        itemEl.className = "media-preview-item";
        itemEl.title = item.file
          ? `${item.file.name} · ${formatSize(item.file.size)}`
          : item.kind === "video"
            ? "已上传的视频"
            : "已上传的图片";

        if (item.kind === "image") {
          const image = document.createElement("img");
          image.src = sourceUrl;
          image.alt = item.file?.name || "已上传的图片";
          itemEl.append(image);
        } else {
          const video = document.createElement("video");
          video.src = sourceUrl;
          video.muted = true;
          video.playsInline = true;
          video.preload = "auto";
          itemEl.append(video);

          const badge = document.createElement("span");
          badge.className = "media-preview-kind";
          badge.textContent = "视频";
          itemEl.append(badge);

          const durationBadge = document.createElement("span");
          durationBadge.className = "media-preview-duration";
          durationBadge.textContent = "--:--";
          itemEl.append(durationBadge);

          video.addEventListener(
            "loadedmetadata",
            () => {
              durationBadge.textContent = formatMediaDuration(video.duration);
              if (Number.isFinite(video.duration) && video.duration > 0) {
                video.currentTime = Math.min(1, video.duration / 2);
              }
            },
            { once: true },
          );

          let frameCaptured = false;
          const captureFrame = () => {
            if (frameCaptured || !itemEl.isConnected) {
              return;
            }
            const width = video.videoWidth;
            const height = video.videoHeight;
            if (!width || !height) {
              return;
            }

            const scale = Math.min(1, 480 / Math.max(width, height));
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.round(width * scale));
            canvas.height = Math.max(1, Math.round(height * scale));
            const context = canvas.getContext("2d");
            if (!context) {
              return;
            }

            try {
              context.drawImage(video, 0, 0, canvas.width, canvas.height);
            } catch {
              return;
            }

            frameCaptured = true;
            const thumbnail = document.createElement("img");
            thumbnail.src = canvas.toDataURL("image/jpeg", 0.82);
            thumbnail.alt = item.file?.name || "已上传的视频";
            video.replaceWith(thumbnail);
          };

          video.addEventListener("loadeddata", captureFrame);
          video.addEventListener("seeked", captureFrame);
          video.addEventListener(
            "error",
            () => {
              durationBadge.textContent = "--:--";
            },
            { once: true },
          );
        }

        if (item.status === "uploading" || item.status === "error") {
          const progressEl = document.createElement("span");
          progressEl.className = "media-preview-progress";
          if (item.status === "error") {
            progressEl.classList.add("is-error");
          }

          const track = document.createElement("span");
          track.className = "media-preview-progress-track";
          const bar = document.createElement("span");
          bar.className = "media-preview-progress-bar";
          bar.style.width =
            item.status === "error" ? "100%" : `${item.progress}%`;
          track.append(bar);

          const text = document.createElement("span");
          text.className = "media-preview-progress-text";
          text.textContent =
            item.status === "error" ? "上传失败" : `${item.progress}%`;

          progressEl.append(track, text);
          itemEl.append(progressEl);
          item.progressEl = progressEl;
          item.progressBar = bar;
          item.progressText = text;
        } else {
          item.progressEl = null;
          item.progressBar = null;
          item.progressText = null;
        }

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "media-preview-remove";
        removeButton.setAttribute("aria-label", "移除这个文件");
        removeButton.textContent = "×";
        removeButton.addEventListener("click", () => removeItem(item));
        itemEl.append(removeButton);

        grid.append(itemEl);
        item.el = itemEl;
      });

      if (items.length > 0 && items.length < maxFiles) {
        const addButton = document.createElement("button");
        addButton.type = "button";
        addButton.className = "media-preview-add";
        addButton.setAttribute("aria-label", "继续添加图片或视频");

        const icon = document.createElement("span");
        icon.className = "media-preview-add-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.textContent = "+";

        const label = document.createElement("span");
        label.textContent = "添加";

        addButton.append(icon, label);
        addButton.addEventListener("click", () => input.click());
        grid.append(addButton);
      }

      preview.hidden = items.length === 0;
      if (clearButton) {
        clearButton.hidden = items.length === 0;
      }

      refreshSummary();
    };

    const addIncomingFiles = (incoming) => {
      const problems = [];
      const known = new Set(
        items.filter((item) => item.key).map((item) => item.key),
      );
      let totalSize = items.reduce(
        (total, item) => total + (item.size || 0),
        0,
      );

      incoming.forEach((file) => {
        const key = fileKey(file);
        if (known.has(key)) {
          return;
        }

        const { kind, error } = validateFile(file);
        if (error) {
          problems.push(error);
          return;
        }
        if (items.length >= maxFiles) {
          problems.push(`最多只能选择 ${maxFiles} 个文件。`);
          return;
        }
        if (maxTotalBytes > 0 && totalSize + file.size > maxTotalBytes) {
          problems.push(
            `图片和视频总大小不能超过 ${formatSize(maxTotalBytes)}。`,
          );
          return;
        }

        known.add(key);
        totalSize += file.size;
        items.push({
          id: nextId,
          file,
          key,
          kind,
          size: file.size,
          status: "uploading",
          progress: 0,
          token: null,
          url: "",
          objectUrl: URL.createObjectURL(file),
          hidden: null,
          xhr: null,
        });
        nextId += 1;
      });

      render();
      items.forEach((item) => {
        if (item.status === "uploading" && !item.xhr) {
          startUpload(item);
        }
      });

      if (problems.length) {
        setStatus(problems[0], "error");
      }
    };

    Array.from(
      uploadedContainer?.querySelectorAll('input[name="uploaded_media"]') ||
        [],
    ).forEach((hidden) => {
      items.push({
        id: nextId,
        file: null,
        key: null,
        kind: hidden.dataset.kind === "video" ? "video" : "image",
        size: Number(hidden.dataset.size) || 0,
        status: "done",
        progress: 100,
        token: hidden.value,
        url: hidden.dataset.url || "",
        objectUrl: null,
        hidden,
        xhr: null,
      });
      nextId += 1;
    });

    input.addEventListener("change", () => {
      const incoming = Array.from(input.files || []);
      clearNativeInput();
      if (incoming.length > 0) {
        addIncomingFiles(incoming);
      }
    });

    clearButton?.addEventListener("click", () => {
      items.forEach((item) => {
        if (item.xhr) {
          item.xhr.abort();
          item.xhr = null;
        }
        if (item.token) {
          discardToken(item.token);
        }
        if (item.objectUrl) {
          URL.revokeObjectURL(item.objectUrl);
        }
        item.hidden?.remove();
      });
      items.length = 0;
      input.value = "";
      render();
      setStatus("");
      input.focus();
    });

    form.addEventListener("submit", (event) => {
      if (items.some((item) => item.status === "uploading")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setStatus("文件还在上传中，请等待上传完成后再发布。", "error");
        updateSubmitState();
        return;
      }
      if (items.some((item) => item.status === "error")) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setStatus("有文件上传失败，请先移除失败的文件。", "error");
      }
    });

    render();
  });

  document.querySelectorAll("[data-video-thumb]").forEach((figure) => {
    const video = figure.querySelector("video");
    if (!video) {
      return;
    }

    const overlay = document.createElement("button");
    overlay.type = "button";
    overlay.className = "video-thumb-overlay";
    overlay.setAttribute("aria-label", "播放视频");

    const image = document.createElement("img");
    image.alt = "";
    image.setAttribute("aria-hidden", "true");
    overlay.append(image);

    const playIcon = document.createElement("span");
    playIcon.className = "video-thumb-play";
    playIcon.setAttribute("aria-hidden", "true");
    playIcon.textContent = "▶";
    overlay.append(playIcon);

    const durationBadge = document.createElement("span");
    durationBadge.className = "video-thumb-duration";
    durationBadge.textContent = "--:--";
    overlay.append(durationBadge);

    figure.append(overlay);

    let captured = false;
    const captureFrame = () => {
      if (captured) {
        return;
      }
      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) {
        return;
      }

      const scale = Math.min(1, 720 / Math.max(width, height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const context = canvas.getContext("2d");
      if (!context) {
        return;
      }

      try {
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
      } catch {
        return;
      }

      captured = true;
      image.src = canvas.toDataURL("image/jpeg", 0.85);
      overlay.classList.add("is-ready");
      video.preload = "metadata";
    };

    video.preload = "auto";
    video.addEventListener("loadedmetadata", () => {
      durationBadge.textContent = formatMediaDuration(video.duration);
      if (Number.isFinite(video.duration) && video.duration > 0) {
        video.currentTime = Math.min(1, video.duration / 2);
      }
    });
    video.addEventListener("loadeddata", captureFrame);
    video.addEventListener("seeked", captureFrame);

    overlay.addEventListener("click", () => {
      overlay.remove();
      const played = video.play();
      if (played && typeof played.catch === "function") {
        played.catch(() => {});
      }
    });
  });

  document.querySelectorAll("[data-video-poster]").forEach((container) => {
    const video = container.querySelector("video");
    if (!video) {
      return;
    }

    const playIcon = document.createElement("span");
    playIcon.className = "video-poster-play";
    playIcon.setAttribute("aria-hidden", "true");
    playIcon.textContent = "▶";
    container.append(playIcon);

    const durationBadge = document.createElement("span");
    durationBadge.className = "video-poster-duration";
    durationBadge.textContent = "--:--";
    container.append(durationBadge);

    let started = false;
    let captured = false;

    const captureFrame = () => {
      if (captured) {
        return;
      }
      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) {
        return;
      }

      const scale = Math.min(1, 720 / Math.max(width, height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      const context = canvas.getContext("2d");
      if (!context) {
        return;
      }

      try {
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
      } catch {
        return;
      }

      captured = true;
      const poster = document.createElement("img");
      poster.src = canvas.toDataURL("image/jpeg", 0.85);
      poster.alt = "";
      poster.setAttribute("aria-hidden", "true");
      video.replaceWith(poster);
    };

    const start = () => {
      if (started) {
        return;
      }
      started = true;
      video.preload = "auto";
      video.addEventListener("loadedmetadata", () => {
        durationBadge.textContent = formatMediaDuration(video.duration);
        if (Number.isFinite(video.duration) && video.duration > 0) {
          video.currentTime = Math.min(1, video.duration / 2);
        }
      });
      video.addEventListener("loadeddata", captureFrame);
      video.addEventListener("seeked", captureFrame);
    };

    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        (entries) => {
          entries.forEach((entry) => {
            if (entry.isIntersecting) {
              observer.disconnect();
              start();
            }
          });
        },
        { rootMargin: "300px" },
      );
      observer.observe(container);
    } else {
      start();
    }
  });

  document.querySelectorAll("[data-avatar-input]").forEach((input) => {
    const form = input.closest("form");
    const preview = document.getElementById(input.dataset.previewTarget);
    const fallback = document.getElementById(input.dataset.fallbackTarget);
    const status = form?.querySelector("[data-avatar-status]");
    const submit = form?.querySelector("[data-avatar-submit]");

    if (!form || !preview) {
      return;
    }

    const maxBytes = Number(input.dataset.maxBytes) || 5 * 1024 * 1024;
    const maxMb = Number(input.dataset.maxMb) || 5;
    const allowedExtensions = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp"]);
    const initialStatus = status?.textContent.trim() || "";
    const originalSrc = preview.getAttribute("src") || "";
    let objectUrl = null;

    const formatSize = (bytes) => {
      if (bytes >= 1024 * 1024) {
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
      }
      return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    };

    const setStatus = (message, kind = "") => {
      if (!status) {
        return;
      }
      status.textContent = message;
      status.classList.remove("is-error");
      if (kind) {
        status.classList.add(`is-${kind}`);
      }
    };

    const restoreOriginal = () => {
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
        objectUrl = null;
      }
      if (originalSrc) {
        preview.src = originalSrc;
        preview.hidden = false;
      } else {
        preview.removeAttribute("src");
        preview.hidden = true;
      }
      if (fallback) {
        fallback.hidden = Boolean(originalSrc);
      }
    };

    const reject = (message) => {
      input.value = "";
      restoreOriginal();
      setStatus(message, "error");
      if (submit) {
        submit.disabled = true;
      }
    };

    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (!file) {
        restoreOriginal();
        setStatus(initialStatus);
        if (submit) {
          submit.disabled = true;
        }
        return;
      }

      const dot = file.name.lastIndexOf(".");
      const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
      if (!allowedExtensions.has(extension)) {
        reject("头像仅支持 JPG、PNG、GIF 和 WebP 格式。");
        return;
      }
      if (file.size > maxBytes) {
        reject(`头像大小不能超过 ${maxMb} MB。`);
        return;
      }

      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
      objectUrl = URL.createObjectURL(file);
      preview.src = objectUrl;
      preview.hidden = false;
      if (fallback) {
        fallback.hidden = true;
      }
      setStatus(`已选择：${file.name}（${formatSize(file.size)}）`);
      if (submit) {
        submit.disabled = false;
      }
    });

    form.addEventListener("submit", () => {
      if (submit) {
        submit.disabled = true;
        submit.textContent = "保存中…";
      }
    });
  });

  document.querySelectorAll("[data-char-count-for]").forEach((counter) => {
    const input = document.getElementById(counter.dataset.charCountFor);
    if (!input) {
      return;
    }
    const max = Number(counter.dataset.max) || Number(input.maxLength) || 0;

    const update = () => {
      const length = Array.from(input.value).length;
      counter.textContent = `${length} / ${max}`;
      counter.classList.toggle("is-near-limit", max > 0 && length / max > 0.9);
    };

    input.addEventListener("input", update);
    update();
  });
})();
