window.visionAuth = {
  send: async function (path, body, bearerToken) {
    const headers = {
      "Content-Type": "application/json"
    };

    if (bearerToken) {
      headers["Authorization"] = "Bearer " + bearerToken;
    }

    const response = await fetch(path, {
      method: "POST",
      credentials: "include",
      headers,
      body: body == null ? null : JSON.stringify(body)
    });

    return {
      ok: response.ok,
      status: response.status,
      body: await response.text()
    };
  }
};
