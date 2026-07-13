(function () {
  const root = document.querySelector("[data-webmentions]");
  if (!root) return;

  const target = root.getAttribute("data-url");
  const status = root.querySelector("[data-webmentions-status]");
  const list = root.querySelector("[data-webmentions-list]");
  if (!target || !status || !list) return;

  const groups = {
    replies: { title: "Replies", items: [] },
    mentions: { title: "Mentions", items: [] },
    likes: { title: "Likes", items: [] },
    reposts: { title: "Reposts", items: [] },
  };

  function groupFor(item) {
    const property = item["wm-property"];
    if (property === "like-of") return groups.likes;
    if (property === "repost-of") return groups.reposts;
    if (property === "in-reply-to") return groups.replies;
    return groups.mentions;
  }

  function text(value, fallback) {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
  }

  function appendText(parent, tag, className, value) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = value;
    parent.appendChild(element);
    return element;
  }

  function renderItem(item) {
    const article = document.createElement("article");
    article.className = "webmentions__item";

    const author = item.author || {};
    const source = text(item.url, "#");
    const authorName = text(author.name, "Someone");

    const heading = document.createElement("a");
    heading.className = "webmentions__author";
    heading.href = source;
    heading.rel = "nofollow ugc noopener noreferrer";
    heading.target = "_blank";
    heading.textContent = authorName;
    article.appendChild(heading);

    const published = text(item.published, "");
    if (published) {
      appendText(article, "time", "webmentions__date", published);
    }

    const content = item.content && text(item.content.text, "");
    if (content) {
      appendText(article, "p", "webmentions__content", content);
    }

    return article;
  }

  function render(items) {
    items.forEach((item) => {
      if (item && item["wm-target"] === target) {
        groupFor(item).items.push(item);
      }
    });

    const total = Object.values(groups).reduce((count, group) => count + group.items.length, 0);
    if (!total) {
      status.textContent = "No webmentions yet.";
      return;
    }

    status.hidden = true;
    list.hidden = false;

    Object.values(groups).forEach((group) => {
      if (!group.items.length) return;
      const section = document.createElement("section");
      section.className = "webmentions__group";
      appendText(section, "h3", "webmentions__group-title", group.title);
      group.items.forEach((item) => section.appendChild(renderItem(item)));
      list.appendChild(section);
    });
  }

  const endpoint = "https://webmention.io/api/mentions.jf2?per-page=50&target=" + encodeURIComponent(target);
  fetch(endpoint, { headers: { Accept: "application/json" } })
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error("Webmention request failed"))))
    .then((data) => render(Array.isArray(data.children) ? data.children : []))
    .catch(() => {
      status.textContent = "Webmentions are temporarily unavailable.";
    });
})();
