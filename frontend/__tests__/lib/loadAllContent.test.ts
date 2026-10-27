/* eslint-disable @typescript-eslint/no-explicit-any */
import { contentAPI } from "../../lib/api";
import { loadAllContent } from "../../lib/loadAllContent";

jest.mock("../../lib/api");
const api = contentAPI as jest.Mocked<typeof contentAPI>;
const items = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ id: `${i}` }));

describe("loadAllContent", () => {
  beforeEach(() => jest.clearAllMocks());

  it("stops when a page is short", async () => {
    api.getAll.mockResolvedValueOnce({ items: items(100), total: 500 } as any);
    api.getAll.mockResolvedValueOnce({ items: items(3), total: 500 } as any);
    const { items: all } = await loadAllContent();
    expect(all).toHaveLength(103);
    expect(api.getAll).toHaveBeenCalledTimes(2);
  });

  it("caps the number of pages", async () => {
    api.getAll.mockResolvedValue({
      items: items(100),
      total: 10_000_000,
    } as any);
    await loadAllContent();
    expect(api.getAll).toHaveBeenCalledTimes(100);
  });
});
