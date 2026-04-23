/** 族譜中的一位成員 */
export interface FamilyMember {
  user_id: string;
  relationship_type: string | null;
  display_name?: string;   // 後續由後端擴充提供（方案 A）
  picture_url?: string;    // 後續由後端擴充提供（方案 A）
}

/** 完整族譜 */
export interface FamilyTree {
  user_id: string;
  family_members: FamilyMember[];
  created_at: string;
  updated_at: string;
}

/** GET /family-tree/me 回應 */
export interface GetFamilyTreeResponse {
  family_tree: FamilyTree;
}

/** POST /family-tree/invite 回應 */
export interface SendInvitationResponse {
  invite_id: string;
  invite_url: string;
}

/** 稱謂中文對照表 */
export const RELATIONSHIP_LABEL: Record<string, string> = {
  parent:      '父/母',
  child:       '子/女',
  spouse:      '配偶',
  sibling:     '兄弟姊妹',
  grandparent: '祖父母',
  grandchild:  '孫子女',
  other:       '其他',
};
